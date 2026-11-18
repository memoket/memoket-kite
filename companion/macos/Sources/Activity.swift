import AppKit
import ApplicationServices
import Carbon.HIToolbox

// App identity and editor focus are separate signals. A normal browser window
// alone cannot identify Gmail or a reply editor.
enum ReplyChannel: String, CaseIterable, Identifiable, Decodable {
    case slack = "Slack"
    case lark = "飞书"
    case gmail = "Gmail"
    case x = "X"

    var id: String { rawValue }

    static func foreground(bundle: String?) -> ReplyChannel? {
        switch bundle {
        case "com.tinyspeck.slackmacgap": .slack
        case "com.electron.lark", "com.bytedance.macos.lark", "com.bytedance.macos.feishu": .lark
        default: nil
        }
    }
}

enum ReplyFillIssue: String, Error {
    case activate = "原应用没有回到前台。请切回去再试。"
    case focus = "请在原应用点一下同一个回复框，把光标放在末尾，再回到 KITE 点击填入。"
    case quote = "现在的回复引用和原消息不一致。请回原消息重新起草。"
    case draftExists = "这个回复框已有草稿。KITE 保留了里面的内容。"
    case rejected = "原应用没有接受写入。草稿仍在 KITE 中。"
}

/// A write is valid only while the very same native editor and quote still exist.
struct NativeReplyTarget {
    let pid: pid_t
    let window: AXUIElement
    let composer: AXUIElement
    let channel: ReplyChannel
    let incoming: String
    let author: String
}

@MainActor
final class ForegroundActivity {
    private static let editorSeparators = CharacterSet.whitespacesAndNewlines
        .union(CharacterSet(charactersIn: "\u{200B}\u{FEFF}"))
    private var lastHint: [ReplyChannel: Date] = [:]
    private var focusSince: Date?
    private var focusPID: pid_t?
    private var focusChannel: ReplyChannel?

    static func isBrowser(_ bundle: String?) -> Bool {
        ["com.apple.Safari", "com.google.Chrome", "com.microsoft.edgemac", "company.thebrowser.Browser"].contains(bundle ?? "")
    }

    static func browserChannel(_ origin: URL?) -> ReplyChannel? {
        origin?.scheme == "https" && origin?.host == "mail.google.com" ? .gmail : nil
    }

    func channel(for app: NSRunningApplication?, allowBrowser: Bool) -> ReplyChannel? {
        guard let app else { return nil }
        if let native = ReplyChannel.foreground(bundle: app.bundleIdentifier) { return native }
        if allowBrowser, let focused = focusedElement(app) { return sourceChannel(app, from: focused) }
        return nil
    }

    func hint(enabled: Bool, now: Date = Date()) -> (ReplyChannel, NSRunningApplication)? {
        guard enabled, let app = NSWorkspace.shared.frontmostApplication, let channel = composingChannel(app) else {
            focusSince = nil
            focusPID = nil
            focusChannel = nil
            return nil
        }
        if focusPID != app.processIdentifier || focusChannel != channel {
            focusPID = app.processIdentifier
            focusChannel = channel
            focusSince = now
        }
        guard let since = focusSince, now.timeIntervalSince(since) >= 3,
              lastHint[channel].map({ now.timeIntervalSince($0) >= 1200 }) ?? true else { return nil }
        lastHint[channel] = now
        return (channel, app)
    }

    private func attribute(_ name: String, from element: AXUIElement) -> CFTypeRef? {
        var result: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &result) == .success else { return nil }
        return result
    }

    private func element(_ value: CFTypeRef?) -> AXUIElement? {
        guard let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return unsafeDowncast(value, to: AXUIElement.self)
    }

    private func focusedElement(_ app: NSRunningApplication) -> AXUIElement? {
        guard AXIsProcessTrusted(), ReplyChannel.foreground(bundle: app.bundleIdentifier) != nil
                || Self.isBrowser(app.bundleIdentifier), !app.isTerminated else { return nil }
        return element(attribute(kAXFocusedUIElementAttribute, from: AXUIElementCreateApplication(app.processIdentifier)))
    }

    private func role(_ element: AXUIElement) -> String? {
        attribute(kAXRoleAttribute, from: element) as? String
    }

    private func editor(_ element: AXUIElement) -> Bool {
        var editable: DarwinBoolean = false
        return role(element) == (kAXTextAreaRole as String)
            && AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &editable) == .success
            && editable.boolValue
    }

    private func sourceChannel(_ app: NSRunningApplication, from focused: AXUIElement) -> ReplyChannel? {
        if let native = ReplyChannel.foreground(bundle: app.bundleIdentifier) { return native }
        guard Self.isBrowser(app.bundleIdentifier) else { return nil }
        var node: AXUIElement? = focused
        for _ in 0..<16 {
            guard let current = node else { return nil }
            if role(current) == "AXWebArea" {
                let raw = attribute(kAXURLAttribute, from: current)
                let url = (raw as? URL) ?? (raw as? String).flatMap(URL.init(string:))
                if let url { return Self.browserChannel(url) }
            }
            node = element(attribute(kAXParentAttribute, from: current))
        }
        return nil
    }

    private func composingChannel(_ app: NSRunningApplication) -> ReplyChannel? {
        // Passive checks only inspect position and type. Message text is
        // read by incomingMessage after the user requests a draft.
        guard let focused = focusedElement(app), editor(focused) else { return nil }
        return sourceChannel(app, from: focused)
    }

    private func text(_ value: CFTypeRef?) -> String? {
        let raw = (value as? String) ?? (value as? NSAttributedString)?.string
        let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty ? nil : String(trimmed.unicodeScalars.prefix(6000))
    }

    private func selectedText(in node: AXUIElement) -> String? {
        if let selection = text(attribute(kAXSelectedTextAttribute, from: node)) { return selection }
        guard role(node) == "AXWebArea",
              let range = attribute("AXSelectedTextMarkerRange", from: node) else { return nil }
        var selection: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(node, "AXStringForTextMarkerRange" as CFString,
                                                         range, &selection) == .success else { return nil }
        return text(selection)
    }

    private func larkSelection(in app: NSRunningApplication) -> String? {
        let application = AXUIElementCreateApplication(app.processIdentifier)
        guard let window = element(attribute(kAXFocusedWindowAttribute, from: application)) else { return nil }
        var containers = [window]
        var selections: Set<String> = []
        var visited = 0
        while let node = containers.popLast(), visited < 160 {
            visited += 1
            if role(node) == "AXWebArea" {
                if let selection = selectedText(in: node) { selections.insert(selection) }
            } else if let children = attribute(kAXChildrenAttribute, from: node) as? [AXUIElement] {
                containers.append(contentsOf: children.reversed())
            }
        }
        return selections.count == 1 ? selections.first : nil
    }

    func incomingMessage(in app: NSRunningApplication) -> (ReplyChannel, String)? {
        guard AXIsProcessTrusted(), !app.isTerminated else { return nil }
        let focused = focusedElement(app)
        let channel = ReplyChannel.foreground(bundle: app.bundleIdentifier)
            ?? focused.flatMap { sourceChannel(app, from: $0) }
        guard let channel, focused.map({ !editor($0) }) ?? true else { return nil }
        var node: AXUIElement? = focused
        for _ in 0..<8 {
            guard let current = node else { break }
            if let selection = selectedText(in: current) {
                return (channel, selection)
            }
            if role(current) == "AXWebArea" { break }
            node = element(attribute(kAXParentAttribute, from: current))
        }
        // Lark embeds the chat in its own WebArea. The native parent window
        // can retain focus while the web renderer owns the selection.
        if channel == .lark, let selection = larkSelection(in: app) { return (channel, selection) }
        // A native chat may focus an individual read-only message body.
        // Never infer an incoming message from an entire window or thread.
        if let focused, role(focused) == (kAXStaticTextRole as String),
           let body = text(attribute(kAXValueAttribute, from: focused)) {
            return (channel, body)
        }
        return nil
    }

    private func compact(_ value: String) -> String {
        value.components(separatedBy: Self.editorSeparators).joined()
    }

    private func nativeQuote(in node: AXUIElement) -> (author: String, body: String)? {
        for name in [kAXValueAttribute, kAXTitleAttribute, kAXDescriptionAttribute,
                     kAXHelpAttribute, "AXValueDescription", "AXAttributedValue"] {
            guard let raw = text(attribute(name, from: node)) else { continue }
            let label = raw.trimmingCharacters(in: Self.editorSeparators)
            let prefix = ["回复", "Reply to"].compactMap { label.range(of: $0) }.first {
                label.distance(from: label.startIndex, to: $0.lowerBound) <= 8
            }
            guard let prefix else { continue }
            let rest = label[prefix.upperBound...]
            guard let colon = rest.firstIndex(where: { $0 == ":" || $0 == "：" }) else { continue }
            let author = rest[..<colon].trimmingCharacters(in: .whitespacesAndNewlines)
            let body = rest[rest.index(after: colon)...].trimmingCharacters(in: .whitespacesAndNewlines)
            if !author.isEmpty { return (author, body) }
        }
        return nil
    }

    private func normalizedQuote(_ text: String) -> String {
        compact(text).replacingOccurrences(of: "•", with: "-")
    }

    private func adjacentReplyQuote(for editor: AXUIElement) -> (author: String, body: String)? {
        var branch = editor
        for _ in 0..<2 {
            guard let parent = element(attribute(kAXParentAttribute, from: branch)), role(parent) != "AXWebArea",
                  let children = attribute(kAXChildrenAttribute, from: parent) as? [AXUIElement],
                  let index = children.firstIndex(where: { CFEqual($0, branch) }) else { return nil }
            // A quote must be in the branch directly preceding the composer.
            var nearby = children.prefix(index).suffix(1).map { ($0, 0) }
            var visited = 0
            while let (node, depth) = nearby.popLast(), visited < 24 {
                visited += 1
                if let quote = nativeQuote(in: node), !quote.body.isEmpty { return quote }
                if depth < 2, let children = attribute(kAXChildrenAttribute, from: node) as? [AXUIElement] {
                    nearby.append(contentsOf: children.map { ($0, depth + 1) })
                }
            }
            branch = parent
        }
        return nil
    }

    private func axPoint(_ node: AXUIElement, _ key: String) -> CGPoint? {
        guard let raw = attribute(key, from: node), CFGetTypeID(raw) == AXValueGetTypeID() else { return nil }
        var point = CGPoint.zero
        return AXValueGetValue(unsafeDowncast(raw, to: AXValue.self), .cgPoint, &point) ? point : nil
    }

    private func visibleFeishuQuote(for editor: AXUIElement, in window: AXUIElement) -> (author: String, body: String)? {
        let nodes = replyTree(in: window)
        guard let index = nodes.firstIndex(where: { CFEqual($0, editor) }) else { return nil }
        let nearby = nodes[max(0, index - 15) ..< index]
        let adjacentLabels = nearby.indices.compactMap { offset -> (author: String, body: String)? in
            guard let quote = nativeQuote(in: nodes[offset]) else { return nil }
            if !quote.body.isEmpty { return quote }
            // Feishu can expose the sender label and the original text as separate elements.
            for sibling in nodes[(offset + 1) ..< min(offset + 4, index)] {
                guard role(sibling) == (kAXStaticTextRole as String),
                      let raw = text(attribute(kAXValueAttribute, from: sibling)),
                      compact(raw).count >= 4, nativeQuote(in: sibling) == nil
                else { continue }
                return (quote.author, raw)
            }
            return nil
        }
        let quote: (author: String, body: String)?
        if let first = adjacentLabels.first,
           adjacentLabels.allSatisfy({ $0.author == first.author && $0.body == first.body }) {
            quote = first
        } else {
            quote = nil
        }
        if let quote {
            let current = value(editor).map(compact) ?? ""
            if current.hasPrefix("@" + compact(quote.author)) { return quote }
        }
        guard let area = axPoint(editor, kAXPositionAttribute) else { return nil }
        let geometric = nodes.compactMap { node -> (author: String, body: String)? in
            guard let point = axPoint(node, kAXPositionAttribute),
                  (-12...100).contains(area.y - point.y),
                  (-12...210).contains(point.x - area.x) else { return nil }
            guard let quote = nativeQuote(in: node), !quote.body.isEmpty else { return nil }
            return quote
        }
        guard let first = geometric.first,
              geometric.allSatisfy({ $0.author == first.author && $0.body == first.body }) else { return nil }
        return first
    }

    private func replyQuote(for editor: AXUIElement, window: AXUIElement, channel: ReplyChannel) -> (author: String, body: String)? {
        if let quote = adjacentReplyQuote(for: editor) { return quote }
        // The Feishu renderer can flatten the reply strip and the editor into separate AX branches.
        return channel == .lark ? visibleFeishuQuote(for: editor, in: window) : nil
    }

    private func matchesReply(_ target: NativeReplyTarget) -> Bool {
        guard let quote = replyQuote(for: target.composer, window: target.window, channel: target.channel) else {
            return false
        }
        return quote.author == target.author && normalizedQuote(quote.body) == normalizedQuote(target.incoming)
    }

    private func focusedWindow(_ app: NSRunningApplication) -> AXUIElement? {
        let application = AXUIElementCreateApplication(app.processIdentifier)
        if let focused = element(attribute(kAXFocusedWindowAttribute, from: application)) {
            return focused
        }
        if let main = element(attribute(kAXMainWindowAttribute, from: application)) {
            return main
        }
        let windows = attribute(kAXWindowsAttribute, from: application) as? [AXUIElement] ?? []
        return windows.count == 1 ? windows.first : nil
    }

    private func editableSelectedText(_ node: AXUIElement) -> Bool {
        var allowed: DarwinBoolean = false
        return AXUIElementIsAttributeSettable(node, kAXSelectedTextAttribute as CFString, &allowed) == .success
            && allowed.boolValue
    }

    private func replyTree(in window: AXUIElement) -> [AXUIElement] {
        var nodes = [window]
        var ordered: [AXUIElement] = []
        var visited = 0
        while let node = nodes.popLast(), visited < 1600 {
            visited += 1
            let kind = role(node)
            ordered.append(node)
            if kind == "AXWebArea",
               let url = attribute(kAXURLAttribute, from: node) as? URL,
               url.isFileURL, !url.path.contains("messenger-chat") { continue }
            if let children = attribute(kAXChildrenAttribute, from: node) as? [AXUIElement] {
                nodes.append(contentsOf: children.reversed())
            }
        }
        return ordered
    }

    private func replyEditors(in window: AXUIElement) -> [AXUIElement] {
        replyTree(in: window).filter { role($0) == (kAXTextAreaRole as String) && value($0) != nil }
    }

    func currentLarkQuote(in app: NSRunningApplication) -> String? {
        guard AXIsProcessTrusted(), ReplyChannel.foreground(bundle: app.bundleIdentifier) == .lark,
              let window = focusedWindow(app) else { return nil }
        let editors = replyEditors(in: window)
        let quotes = editors.compactMap { replyQuote(for: $0, window: window, channel: .lark) }
        return quotes.count == 1 ? quotes.first?.body : nil
    }

    func bindNativeReply(app: NSRunningApplication, channel: ReplyChannel, incoming: String) -> NativeReplyTarget? {
        guard AXIsProcessTrusted(), ReplyChannel.foreground(bundle: app.bundleIdentifier) == channel,
              let window = focusedWindow(app), compact(incoming).count >= 4 else { return nil }
        let targets = replyEditors(in: window).compactMap { composer -> (AXUIElement, String)? in
            guard let quote = replyQuote(for: composer, window: window, channel: channel),
                  normalizedQuote(quote.body) == normalizedQuote(incoming) else { return nil }
            return (composer, quote.author)
        }
        guard targets.count == 1, let (composer, author) = targets.first else { return nil }
        return NativeReplyTarget(pid: app.processIdentifier, window: window, composer: composer,
                                 channel: channel, incoming: incoming, author: author)
    }

    private func value(_ node: AXUIElement) -> String? {
        let content = attribute(kAXValueAttribute, from: node)
        return (content as? String) ?? (content as? NSAttributedString)?.string
    }

    private func documentCaret(_ composer: AXUIElement) -> CFRange? {
        guard let raw = attribute(kAXSelectedTextRangeAttribute, from: composer),
              CFGetTypeID(raw) == AXValueGetTypeID() else { return nil }
        var range = CFRange()
        return AXValueGetValue(unsafeDowncast(raw, to: AXValue.self), .cfRange, &range) ? range : nil
    }

    func canInsertAtCaret(in target: NativeReplyTarget) -> Bool {
        editableSelectedText(target.composer)
    }

    func canDrop(_ draft: String, in target: NativeReplyTarget, app: NSRunningApplication) -> Bool {
        guard target.channel == .lark, !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !app.isTerminated, app.processIdentifier == target.pid,
              let window = focusedWindow(app), CFEqual(target.window, window),
              matchesReply(target), let current = value(target.composer) else { return false }
        let wording = compact(current)
        return wording.isEmpty || wording == "@" + compact(target.author)
    }

    func fillNativeReply(app: NSRunningApplication, target: NativeReplyTarget, draft: String) async throws {
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == target.pid,
              app.processIdentifier == target.pid, ReplyChannel.foreground(bundle: app.bundleIdentifier) == target.channel,
              let window = focusedWindow(app), CFEqual(window, target.window) else { throw ReplyFillIssue.activate }
        let composer = target.composer
        guard matchesReply(target) else { throw ReplyFillIssue.quote }
        if focusedElement(app).map({ CFEqual($0, composer) }) != true {
            guard AXUIElementSetAttributeValue(composer, kAXFocusedAttribute as CFString, kCFBooleanTrue) == .success,
                  focusedElement(app).map({ CFEqual($0, composer) }) == true,
                  matchesReply(target) else { throw ReplyFillIssue.focus }
        }
        guard let current = value(composer) else { throw ReplyFillIssue.focus }
        let body = compact(current)
        let mention = "@" + compact(target.author)
        guard body.isEmpty || (target.channel == .lark && body == mention) else { throw ReplyFillIssue.draftExists }
        let insertion = body.isEmpty || current.last?.isWhitespace == true ? draft : " " + draft
        guard let range = documentCaret(composer), editableSelectedText(composer) else {
            throw ReplyFillIssue.rejected
        }
        let text = current as NSString
        guard range.length == 0, (0...text.length).contains(range.location),
              compact(text.substring(from: range.location)).isEmpty else { throw ReplyFillIssue.focus }
        guard AXUIElementSetAttributeValue(composer, kAXSelectedTextAttribute as CFString,
                                           insertion as CFString) == .success else {
            throw ReplyFillIssue.rejected
        }
        for _ in 0..<5 {
            if value(composer).map(compact) == body + compact(draft), matchesReply(target) { return }
            try await Task.sleep(for: .milliseconds(45))
        }
        throw ReplyFillIssue.rejected
    }

}

@MainActor
final class DraftShortcut {
    private var handler: EventHandlerRef?
    private var reference: EventHotKeyRef?
    var onTrigger: (() -> Void)?

    func register() -> Bool {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let status = InstallEventHandler(GetApplicationEventTarget(), { _, _, user in
            guard let user else { return OSStatus(eventNotHandledErr) }
            let shortcut = Unmanaged<DraftShortcut>.fromOpaque(user).takeUnretainedValue()
            MainActor.assumeIsolated { shortcut.onTrigger?() }
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), &handler)
        guard status == noErr else { return false }
        let key = EventHotKeyID(signature: 0x4B495445, id: 1)
        return RegisterEventHotKey(UInt32(kVK_ANSI_R), UInt32(controlKey | optionKey), key,
                                   GetApplicationEventTarget(), 0, &reference) == noErr
    }

    func stop() {
        if let reference { UnregisterEventHotKey(reference) }
        if let handler { RemoveEventHandler(handler) }
        reference = nil
        handler = nil
    }
}
