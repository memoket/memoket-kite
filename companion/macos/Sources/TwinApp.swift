import AppKit
import ApplicationServices
import OSLog
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class TwinModel: ObservableObject {
    static let home = FileManager.default.homeDirectoryForCurrentUser
    private let defaults = UserDefaults.standard
    private let recorder = Recorder()
    let activity = ForegroundActivity()
    @Published var executable: String
    @Published var workspace: String
    @Published var project: String
    @Published var whisperModel: String
    @Published var whisper: String
    @Published var replyStyle = ""
    @Published var subject: String
    @Published var watchTask = ""
    @Published var detectReplyEditors = false
    @Published private(set) var editorPermissionGranted = false
    @Published private(set) var screenCaptureGranted = false
    @Published private(set) var screenPermissionNeeded = false
    @Published var nextWork: Quote?
    @Published var page = "现在"
    @Published var quickMenuVisible = false
    @Published var replyChannel: ReplyChannel = .lark
    @Published var replyInput = ""
    @Published var replyDraft = ""
    @Published var replyEvidence: [Quote] = []
    @Published var replySubject = ""
    @Published var replyTargetAvailable = false
    private var replyApp: NSRunningApplication?
    @Published private(set) var browserReply: BrowserReply?
    private var nativeTarget: NativeReplyTarget?
    @Published private var nativeFillRejected = false
    private var foregroundBeforePanel: NSRunningApplication?
    private var lastNativeReplyApp: NSRunningApplication?
    @Published var shortcutAvailable = false
    var onNewWork: ((Quote) -> Void)?
    @Published var host = "codex"
    @Published var avatarShade: TwinShade = .iris
    @Published var displayLanguage: TwinLanguage = .chinese {
        didSet {
            defaults.set(displayLanguage.rawValue, forKey: "display-language")
            if displayLanguage != oldValue { onDisplayLanguageChange?() }
        }
    }
    var onDisplayLanguageChange: (() -> Void)?
    @Published var sourceCount = 0
    @Published var version: Int?
    private var workspaceID: String?
    private var profileSettingsWorkspace = ""
    @Published var items: [Original] = []
    @Published var reminders: [Quote] = []
    @Published var evidence: [Quote] = []
    @Published var skills: [SavedSkill] = []
    @Published var pending: Capture?
    @Published private(set) var memoketRecordings: [MemoketRecording]?
    @Published private(set) var memoketNextOffset: Int?
    @Published var note = ""
    @Published var noteIsSelf = false
    @Published var selfSpeakers = ""
    @Published var profile: [ProfileEvidence] = []
    @Published var useProfileAppearance = true
    @Published var task = ""
    @Published var result = ""
    @Published var lastTask = ""
    @Published var handedOff = false
    @Published var includeReviewedResult = false
    private(set) var reviewedResult = ""
    private var handedOffQuoteID: String?
    @Published var selectedQuote: Quote?
    @Published var skillName = ""
    @Published var skillDescription = ""
    @Published var skillSteps = ""
    @Published var completed: Set<String> = []
    @Published var accepted = false
    @Published var working = false
    @Published var recording = false
    @Published var message = ""
    @Published private(set) var operationFailed = false
    @Published var settings = false
    @Published var settingsSection: TwinSettingsSection = .workspace
    @Published var teach = false
    var hideForCapture: (() -> Void)?
    var showControls: (() -> Void)?
    var dismissControls: (() -> Void)?

    init() {
        let arguments = ProcessInfo.processInfo.arguments
        let environment = ProcessInfo.processInfo.environment
        func argument(_ flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag), index + 1 < arguments.count else { return nil }
            return arguments[index + 1]
        }
        let defaultWorkspace = Self.home.appendingPathComponent("Library/Application Support/KITE Twin/memory").path
        executable = argument("--kite-tool") ?? environment["KITE_TOOL_BIN"] ?? defaults.string(forKey: "kite-tool") ??
            ["\(Self.home.path)/.local/bin/kite-tool", "/opt/homebrew/bin/kite-tool", "/usr/local/bin/kite-tool"]
                .first { FileManager.default.isExecutableFile(atPath: $0) } ?? "\(Self.home.path)/.local/bin/kite-tool"
        workspace = argument("--workspace") ?? environment["KITE_DATA_DIR"] ?? defaults.string(forKey: "workspace") ?? defaultWorkspace
        project = argument("--project") ?? defaults.string(forKey: "project") ?? ""
        host = defaults.string(forKey: "host") == "claude" ? "claude" : "codex"
        whisperModel = defaults.string(forKey: "whisper") ?? ""
        whisper = defaults.string(forKey: "whisper-cli") ?? ""
        subject = argument("--watch-subject") ?? defaults.string(forKey: "subject") ?? ""
        watchTask = argument("--watch-task") ?? defaults.string(forKey: "watch-task") ?? ""
        detectReplyEditors = defaults.bool(forKey: "detect-reply-editors")
        accepted = defaults.bool(forKey: "accepted:\(workspace)")
        completed = Set(defaults.stringArray(forKey: "completed:\(workspace)") ?? [])
        avatarShade = defaults.string(forKey: "avatar-shade").flatMap(TwinShade.init(rawValue:)) ?? .iris
        displayLanguage = defaults.string(forKey: "display-language").flatMap(TwinLanguage.init(rawValue:)) ?? .chinese
        loadStyle()
        loadProfileSettings()
    }

    var bridge: Bridge { Bridge(executable: executable, workspace: workspace) }
    var outstanding: [Quote] { reminders.filter { !completed.contains($0.id) } }
    var evolution: Int { sourceCount == 0 ? 0 : accepted ? 3 : sourceCount == 1 ? 1 : 2 }
    var indexingModel: String { host == "codex" ? "Terra" : "Haiku" }
    var appearanceTraits: Set<String> { useProfileAppearance ? Set(profile.map(\.field)) : [] }
    var hostName: String { host == "codex" ? "Codex" : "Claude Code" }
    var configuredWatch: Bool {
        !subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
    var canFillReply: Bool {
        if browserReply?.isConnected == true { return true }
        return !nativeFillRejected && nativeTarget.map { activity.canInsertAtCaret(in: $0) } == true
    }
    var canDragReply: Bool { nativeTarget?.channel == .lark && !replyDraft.isEmpty }

    func replyDragProvider() -> NSItemProvider {
        guard let nativeTarget, let replyApp,
              activity.canDrop(replyDraft, in: nativeTarget, app: replyApp) else {
            message = "引用已变化或回复框已有内容，请回到飞书核对，再重新起草。"
            return NSItemProvider()
        }
        return NSItemProvider(object: replyDraft as NSString)
    }

    func openSettings(_ section: TwinSettingsSection) {
        settingsSection = section
        checkEditorPermission()
        if section == .capture { checkScreenPermission() }
        settings = true
    }

    func checkEditorPermission() {
        let allowed = AXIsProcessTrusted()
        if editorPermissionGranted != allowed { editorPermissionGranted = allowed }
    }

    func setReplyEditorHints(_ enabled: Bool) {
        detectReplyEditors = enabled
        defaults.set(enabled, forKey: "detect-reply-editors")
        checkEditorPermission()
    }

    private func saveProfileSettings() {
        defaults.set(selfSpeakers, forKey: "profile-speakers:\(profileSettingsWorkspace)")
        defaults.set(useProfileAppearance, forKey: "profile-appearance:\(profileSettingsWorkspace)")
    }

    func editProfile(_ row: ProfileEvidence, summary: String) {
        perform(status: "正在保存画像修改…") {
            let response = try await self.bridge.call("profile-edit", [
                "field": row.field, "summary": summary,
            ])
            self.update(response)
            self.message = summary.isEmpty ? "已恢复自动生成的画像。" : "画像已更新。"
        }
    }

    private func loadProfileSettings() {
        profileSettingsWorkspace = workspace
        selfSpeakers = defaults.string(forKey: "profile-speakers:\(workspace)") ?? ""
        useProfileAppearance = defaults.object(forKey: "profile-appearance:\(workspace)") as? Bool ?? true
    }

    func configure() {
        let workspaceChanged = defaults.string(forKey: "workspace") != workspace
            || defaults.string(forKey: "kite-tool") != executable
        let projectChanged = defaults.string(forKey: "project") != project
        let watchChanged = defaults.string(forKey: "subject") != subject
            || defaults.string(forKey: "watch-task") != watchTask
        defaults.set(executable, forKey: "kite-tool")
        defaults.set(workspace, forKey: "workspace")
        defaults.set(project, forKey: "project")
        defaults.set(host, forKey: "host")
        defaults.set(whisperModel, forKey: "whisper")
        defaults.set(subject, forKey: "subject")
        defaults.set(watchTask, forKey: "watch-task")
        defaults.set(detectReplyEditors, forKey: "detect-reply-editors")
        defaults.set(whisper, forKey: "whisper-cli")
        saveProfileSettings()
        defaults.set(avatarShade.rawValue, forKey: "avatar-shade")
        if workspaceChanged {
            accepted = defaults.bool(forKey: "accepted:\(workspace)")
            completed = Set(defaults.stringArray(forKey: "completed:\(workspace)") ?? [])
            loadStyle()
            version = nil
            workspaceID = nil
            sourceCount = 0
            items = []
            profile = []
            loadProfileSettings()
        }
        if workspaceChanged || watchChanged {
            reminders = []
            nextWork = nil
            selectedQuote = nil
            version = nil
        }
        if workspaceChanged || projectChanged {
            handedOff = false
            handedOffQuoteID = nil
            evidence = []
            result = ""
            reviewedResult = ""
            includeReviewedResult = false
            skills = []
        }
        perform(status: "正在更新工作区…") {
            try await self.refresh()
            self.message = "设置已保存。"
        }
    }

    func perform(status: String = "", _ action: @escaping @MainActor () async throws -> Void) {
        guard !working else { return }
        message = status
        operationFailed = false
        working = true
        Task {
            defer { working = false }
            do { try await action() } catch {
                operationFailed = true
                message = error.localizedDescription
            }
        }
    }

    func refresh() async throws {
        guard FileManager.default.isExecutableFile(atPath: executable), executable.hasPrefix("/"), workspace.hasPrefix("/") else {
            openSettings(.workspace)
            throw TwinError("请选择本地 kite-tool 和工作区目录。")
        }
        let snapshot = try await bridge.call("status")
        let identity = snapshot.workspaceId
        let initial = version == nil || workspaceID != identity
        if initial { loadProfileSettings() }
        let savedVersion = identity.flatMap { defaults.object(forKey: "seen-version:\($0)") as? Int }
        let prior = workspaceID == identity ? (savedVersion ?? version) : savedVersion
        update(snapshot)
        if prior != version {
            result = ""
            evidence = []
            reviewedResult = ""
            includeReviewedResult = false
        }
        if sourceCount > 0 && prior != version {
            let baseline = prior.flatMap { $0 < (version ?? 0) ? $0 : nil }
            try await scan(since: baseline, notify: baseline != nil)
        } else if sourceCount > 0 && initial {
            try await scan()
            if !subject.isEmpty && !watchTask.isEmpty {
                nextWork = outstanding.sorted { ($0.date ?? "") > ($1.date ?? "") }.first
            }
        } else if sourceCount == 0 {
            reminders = []
            nextWork = nil
            selectedQuote = nil
        }
        acknowledgeVersion()
    }

    private func acknowledgeVersion() {
        if let workspaceID, let version { defaults.set(version, forKey: "seen-version:\(workspaceID)") }
    }

    private func update(_ reply: Reply) {
        workspaceID = reply.workspaceId ?? workspaceID
        sourceCount = reply.sourceCount ?? sourceCount
        items = reply.items ?? items
        profile = reply.profile ?? profile
        version = reply.version ?? version
    }

    func scan(since: Int? = nil, notify: Bool = false) async throws {
        var request: [String: Any] = ["subject": subject]
        if let since {
            request["since"] = since
            request["seen"] = Array(reminders.prefix(100).map(\.id))
        }
        let reply = try await bridge.call("reminders", request)
        let rows = reply.reminders ?? []
        if since == nil {
            reminders = rows
        } else {
            let current = Set(rows.map(\.id))
            let retained = Set(reply.retained ?? [])
            reminders = rows + reminders.filter { !current.contains($0.id) && retained.contains($0.id) }
            if let selection = selectedQuote, !reminders.contains(where: { $0.id == selection.id }) { selectedQuote = nil }
        }
        if let nextWork, !reminders.contains(where: { $0.id == nextWork.id }) { self.nextWork = nil }
        if notify, let recent = rows.first(where: { !completed.contains($0.id) }) {
            nextWork = recent
            onNewWork?(recent)

        }
    }

    func checkScreenPermission() {
        let granted = TwinScreenCapture.isAuthorized
        if screenCaptureGranted != granted { screenCaptureGranted = granted }
        if granted && screenPermissionNeeded { screenPermissionNeeded = false }
    }

    func openScreenPermissionSettings() { TwinScreenCapture.openSettings() }

    func screenshot() {
        perform {
            self.checkScreenPermission()
            guard self.screenCaptureGranted || TwinScreenCapture.requestAccess() else {
                self.screenPermissionNeeded = true
                throw TwinScreenCapture.permissionError
            }
            self.hideForCapture?()
            defer { self.showControls?() }
            let image: CGImage?
            do {
                image = try await TwinScreenCapture.captureSelectedRegion()
            } catch {
                self.checkScreenPermission()
                self.screenPermissionNeeded = !self.screenCaptureGranted
                throw error
            }
            guard let image else {
                self.message = "已取消截屏。"
                return
            }
            self.message = "正在识别截图文字…"
            self.pending = try await Capture.screenshot(image)
            self.note = ""
        }
    }

    func selectFile() {
        let dialog = NSOpenPanel()
        dialog.canChooseFiles = true
        dialog.canChooseDirectories = false
        dialog.allowedContentTypes = [.data]
        dialog.prompt = "预览"
        guard dialog.runModal() == .OK, let url = dialog.url else { return }
        load(url)
    }

    func load(_ url: URL) {
        perform {
            let audio = ["wav", "m4a", "mp3", "flac", "ogg"].contains(url.pathExtension.lowercased())
            self.pending = audio ? Capture(kind: "recording", url: url, title: url.lastPathComponent, date: Date()) :
                try await Capture.document(url)
            self.note = ""
        }
    }

    func installMemoketCLI() {
        perform(status: "正在安装官方 Memoket CLI…") {
            try await MemoketCLI.install()
            self.message = "CLI 已安装。现在可以登录或读取录音。"
        }
    }

    func signInToMemoket() {
        perform(status: "请在浏览器完成 Memoket 登录。") {
            try await MemoketCLI.signIn()
            try await self.fetchMemoketRecordings(offset: 0)
            self.message = ""
        }
    }

    func loadMemoketRecordings(offset: Int = 0) {
        perform(status: "正在读取 Memoket 录音列表…") {
            try await self.fetchMemoketRecordings(offset: offset)
            self.message = ""
        }
    }

    private func fetchMemoketRecordings(offset: Int) async throws {
        let response = try await MemoketCLI.list(offset: offset)
        let existing = offset == 0 ? [] : (memoketRecordings ?? [])
        let known = Set(existing.map(\.id))
        memoketRecordings = existing + response.items.filter { !known.contains($0.id) }
        memoketNextOffset = response.nextOffset.flatMap { $0 > offset ? $0 : nil }
    }

    func previewMemoketRecording(_ recording: MemoketRecording) {
        guard pending == nil, !self.recording else { return }
        perform(status: "正在读取转写原文…") {
            self.pending = try await MemoketCLI.prepare(recording)
            self.note = ""
            self.message = ""
            self.page = "记忆"
        }
    }

    func toggleRecording() {
        perform {
            if self.recording {
                self.recording = false
                self.pending = try await self.recorder.stop()
                self.note = ""
            } else {
                try await self.recorder.start()
                self.recording = true
            }
        }
    }

    func discard() {
        if let scratch = pending?.scratch { try? FileManager.default.removeItem(at: scratch) }
        pending = nil
        note = ""
        noteIsSelf = false
    }

    func transcribe() {
        guard var recording = pending, recording.kind == "recording" else { return }
        guard FileManager.default.fileExists(atPath: whisperModel) else {
            openSettings(.capture)
            return
        }
        perform {
            let reply = try await self.bridge.call("transcribe", recording.payload(note: "", model: self.whisperModel, whisper: self.whisper))
            recording.transcript = reply.transcript
            recording.digest = reply.sourceDigest
            self.pending = recording
        }
    }

    func openOriginal(_ item: Original) {
        perform {
            let folder = try LocalProcess.scratch()
            do {
                let reply = try await self.bridge.call("original", ["id": item.id, "directory": folder.path])
                guard let path = reply.path, NSWorkspace.shared.open(URL(fileURLWithPath: path)) else {
                    throw TwinError("无法打开原件。")
                }
                Task {
                    try? await Task.sleep(for: .seconds(180))
                    try? FileManager.default.removeItem(at: folder)
                }
            } catch {
                try? FileManager.default.removeItem(at: folder)
                throw error
            }
        }
    }

    private var styleKey: String { "style:\(workspace):\(replyChannel.rawValue)" }
    func loadStyle() { replyStyle = defaults.string(forKey: styleKey) ?? "" }
    func saveStyle() { defaults.set(replyStyle, forKey: styleKey); message = "已记住此渠道的回复偏好。" }

    func remember() {
        guard let pending else { return }
        perform(status: "建立索引与英文视图…") {
            let prior = self.version
            let priorProfile = self.profile
            var input = pending.payload(note: self.note, model: self.whisperModel, whisper: self.whisper)
            input["host"] = self.host
            input["allow_provider"] = true
            input["translate_english"] = true
            let names = self.selfSpeakers.split { ",，\n".contains($0) }
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
            input["self_speakers"] = Array(names.filter { name in
                pending.memoketID == nil || name.range(of: "^[A-Za-z]$", options: .regularExpression) == nil
            }.prefix(20))
            input["note_is_self"] = self.noteIsSelf
            let reply = try await self.bridge.call("ingest", input)
            self.update(reply)
            self.discard()
            let base = reply.created == false ? "这份原件已经建过索引。" : "原文和索引已保存，可以在记忆图查看。"
            let profileChanged = !priorProfile.elementsEqual(self.profile) {
                $0.field == $1.field && $0.summary == $1.summary
            }
            self.message = profileChanged ? base + " 画像已自动更新。" : base
            if let next = reply.version, let prior, next > prior {
                try? await self.scan(since: prior, notify: true)
            }
            self.acknowledgeVersion()
        }
    }

    func selectQuote(_ quote: Quote) {
        selectedQuote = quote
        if task.isEmpty { task = "核对这条线索和后续原文。若有明确可执行的更新，请完成并带回检查结果。" }
    }

    var nextWorkTitle: String {
        subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "对话里出现了新的后续线索" : "「\(subject)」有新进展"
    }

    var nextWorkTask: String {
        let chosen = watchTask.trimmingCharacters(in: .whitespacesAndNewlines)
        return chosen.isEmpty ? "核对最新原文和项目现状，找出可以推进的下一步。" : chosen
    }

    func prepareWork(_ quote: Quote) {
        selectedQuote = quote
        task = nextWorkTask
        page = "行动"
    }

    func runTask() {
        guard !project.isEmpty else { openSettings(.workspace); message = "先选择任务要在哪个项目里执行。"; return }
        let request = task
        let destination = hostName
        perform(status: "准备 KITE 引用，打开 \(destination)…") {
            var value: [String: Any] = ["project": self.project, "mode": "execute", "subject": self.subject,
                                        "task": request, "kite_tool": self.executable]
            if let quote = self.selectedQuote { value["citation"] = quote.id }
            let reply = try await self.bridge.call("handoff", value)
            guard let path = reply.projectPath, let prompt = reply.prompt else {
                throw TwinError("KITE 没有返回可交接的任务。")
            }
            if self.host == "codex" {
                let copied = try self.openCodex(project: path, prompt: prompt)
                self.message = copied ? "任务已复制。在 Codex 粘贴并发送。" : "在 Codex 查看并发送任务。"
            } else {
                try await self.openClaude(project: path, prompt: prompt)
                self.message = "任务已复制。在 Claude Code 的项目中粘贴并运行。"
            }
            self.handedOff = true
            self.handedOffQuoteID = value["citation"] as? String
            self.result = ""
            self.evidence = []
            self.lastTask = request
            self.dismissControls?()
        }
    }

    func rememberForeground(_ application: NSRunningApplication? = NSWorkspace.shared.frontmostApplication) {
        if let application, application.processIdentifier != ProcessInfo.processInfo.processIdentifier {
            foregroundBeforePanel = application
            if ReplyChannel.foreground(bundle: application.bundleIdentifier) != nil {
                lastNativeReplyApp = application
            }
        }
    }

    func requestEditorPermission() {
        let prompt = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        _ = AXIsProcessTrustedWithOptions([prompt: true] as CFDictionary)
        checkEditorPermission()
        if !editorPermissionGranted,
           let settingsURL = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(settingsURL)
        }
    }

    func beginBrowserReply(_ request: BrowserReply) {
        guard !working else { request.close(); return }
        browserReply?.close()
        browserReply = request
        nativeTarget = nil
        nativeFillRejected = false
        replyApp = nil
        replyTargetAvailable = true
        page = "回复"
        replyChannel = request.channel
        replyInput = request.text
        replyDraft = ""
        replyEvidence = []
        replySubject = ""
        loadStyle()
        draftReply()
    }

    func discardBrowserReply() {
        browserReply?.close()
        browserReply = nil
    }

    func beginReplyInPreviousApp() {
        let candidates = NSWorkspace.shared.runningApplications.filter {
            ReplyChannel.foreground(bundle: $0.bundleIdentifier) == .lark && !$0.isTerminated
        }
        let lastFeishu = [lastNativeReplyApp, foregroundBeforePanel].compactMap { $0 }.first {
            ReplyChannel.foreground(bundle: $0.bundleIdentifier) == .lark && !$0.isTerminated
        }
        guard let app = lastFeishu ?? (candidates.count == 1 ? candidates.first : nil) else {
            message = "先在飞书打开一条消息的回复框。"
            return
        }
        message = "正在核对飞书的回复引用…"
        NSApp.yieldActivation(to: app)
        app.activate()
        Task { @MainActor in
            for _ in 0..<10 {
                try? await Task.sleep(for: .milliseconds(75))
                if NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier { break }
            }
            guard !self.working else { return }
            guard let quote = self.activity.currentLarkQuote(in: app) else {
                self.message = "没读到回复引用。请先在飞书对原消息点「回复」。"
                self.showControls?()
                return
            }
            self.beginReply(channel: .lark, app: app, suppliedText: quote)
            self.showControls?()
        }
    }

    func beginReply(channel: ReplyChannel?, app: NSRunningApplication?, suppliedText: String? = nil) {
        guard !working else { return }
        discardBrowserReply()
        nativeTarget = nil
        nativeFillRejected = false
        let incomingApp = app.flatMap { $0.processIdentifier == ProcessInfo.processInfo.processIdentifier ? nil : $0 }
        let target = incomingApp ?? foregroundBeforePanel
        replyApp = target
        let sourceChannel = activity.channel(for: target, allowBrowser: true)
        replyTargetAvailable = sourceChannel != nil && target?.isTerminated == false
        page = "回复"
        replyDraft = ""
        replyEvidence = []
        replyInput = ""
        replySubject = ""
        operationFailed = false
        message = ""
        checkEditorPermission()
        if let channel = channel ?? sourceChannel { replyChannel = channel }
        if let suppliedText {
            replyInput = String(suppliedText.trimmingCharacters(in: .whitespacesAndNewlines).unicodeScalars.prefix(6000))
        } else if let target {
            if let (channel, body) = activity.incomingMessage(in: target) {
                replyChannel = channel
                replyInput = body
            } else if replyChannel == .lark, let quote = activity.currentLarkQuote(in: target) {
                replyInput = quote
            }
        }
        if let target, !replyInput.isEmpty {
            nativeTarget = activity.bindNativeReply(app: target, channel: replyChannel, incoming: replyInput)
        }
        loadStyle()
        if !replyInput.isEmpty { draftReply() }
    }

    func returnToReplyApp() {
        if let replyApp, !replyApp.isTerminated {
            dismissControls?()
            replyApp.activate()
        }
    }

    func draftReply() {
        guard !replyInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let destination = replyChannel.rawValue
        let input = replyInput
        let manualTopic = replySubject.trimmingCharacters(in: .whitespacesAndNewlines)
        let watched = subject.trimmingCharacters(in: .whitespacesAndNewlines)
        let topic = manualTopic.isEmpty && !watched.isEmpty && input.localizedCaseInsensitiveContains(watched) ? watched : manualTopic
        let chosenHost = host
        let preference = replyStyle.trimmingCharacters(in: .whitespacesAndNewlines)
        perform(status: "正在为 \(destination) 起草…") {
            var value: [String: Any] = ["host": chosenHost, "project": self.project.isEmpty ? self.workspace : self.project,
                                        "mode": "draft", "channel": destination, "subject": topic,
                                        "task": "回复下列收到的消息。用已确认的证据，不要声称一项工作已完成，除非证据显示已完成。",
                                        "incoming_message": input, "style": preference]
            if self.includeReviewedResult, !self.reviewedResult.isEmpty {
                value["reviewed_result"] = String(self.reviewedResult.prefix(6000))
            }
            let reply = try await self.bridge.call("run", value)
            self.replyDraft = reply.result ?? ""
            self.replyEvidence = reply.evidence ?? []
            let noTopic = reply.subjectMissing == true ? "没找到该主题的原文；已根据收到的消息起草。" : ""
            let labels = (reply.profileUsed ?? []).map { $0 == "communication" ? "沟通偏好" : "工作习惯" }
            let withProfile = labels.isEmpty ? "" : "已向起草模型提供画像中的" + labels.joined(separator: "、") + "。"
            self.message = [noTopic, withProfile].filter { !$0.isEmpty }.joined(separator: " ")
        }
    }

    func fillReply() {
        let draft = replyDraft
        guard !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        if let browserReply {
            perform(status: "正在核对原网页的回复框…") {
                try await browserReply.fill(draft)
                self.browserReply = nil
                self.replyTargetAvailable = false
                self.dismissControls?()
                self.message = "已填入原回复框。请在网页里核对。"
            }
            return
        }
        guard let nativeTarget, let replyApp, !replyApp.isTerminated else { return }
        perform(status: "正在填入回复框…") {
            NSApp.yieldActivation(to: replyApp)
            self.dismissControls?()
            replyApp.activate()
            for _ in 0..<10 {
                try await Task.sleep(for: .milliseconds(80))
                if NSWorkspace.shared.frontmostApplication?.processIdentifier == replyApp.processIdentifier { break }
            }
            do {
                try await self.activity.fillNativeReply(app: replyApp, target: nativeTarget, draft: draft)
            } catch let issue as ReplyFillIssue {
                if issue == .rejected { self.nativeFillRejected = true }
                self.showControls?()
                throw TwinError(issue.rawValue)
            }
            self.message = "草稿已填入，请在原应用确认后发送。"
            self.dismissControls?()
        }
    }

    func copyReply() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(replyDraft, forType: .string)
        message = "草稿已复制。回到 \(replyChannel.rawValue) 粘贴。"
        dismissControls?()
        if let replyApp, !replyApp.isTerminated { replyApp.activate() }
    }

    private func openClaude(project: String, prompt: String) async throws {
        guard let application = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.anthropic.claudefordesktop") else {
            throw TwinError("未找到 Claude 桌面版。")
        }
        let options = NSWorkspace.OpenConfiguration()
        options.activates = true
        _ = try await NSWorkspace.shared.openApplication(at: application, configuration: options)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString("在 Claude Code 中使用项目 \(project)。\n\n\(prompt)", forType: .string)
    }

    private func openCodex(project: String, prompt: String) throws -> Bool {
        var link = URLComponents()
        link.scheme = "codex"
        link.host = "threads"
        link.path = "/new"
        let path = URLQueryItem(name: "path", value: project)
        link.queryItems = [path, URLQueryItem(name: "prompt", value: prompt)]
        // URLSearchParams interprets an unescaped plus as a space.
        link.percentEncodedQuery = link.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        let copyRequired = (link.url?.absoluteString.utf8.count ?? Int.max) > 24000
        if copyRequired {
            link.queryItems = [path]
            link.percentEncodedQuery = link.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        }
        guard let url = link.url, NSWorkspace.shared.urlForApplication(toOpen: url) != nil else {
            throw TwinError("未找到 Codex 桌面版。")
        }
        guard NSWorkspace.shared.open(url) else { throw TwinError("未能打开 Codex。") }
        if copyRequired {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(prompt, forType: .string)
        }
        return copyRequired
    }

    func finish() {
        reviewedResult = result
        if let handedOffQuoteID {
            completed.insert(handedOffQuoteID)
            if nextWork?.id == handedOffQuoteID { nextWork = nil }
            defaults.set(Array(completed).sorted(), forKey: "completed:\(workspace)")
        }
        accepted = true
        defaults.set(true, forKey: "accepted:\(workspace)")
        message = "任务已标记完成。"
    }

    func suggestSkill() {
        perform {
            let request = String("任务\n\(self.lastTask)\n\n结果\n\(self.result)".prefix(11000))
            let response = try await self.bridge.call("run", ["host": self.host, "project": self.project,
                                             "mode": "suggest", "task": request])
            self.skillName = response.skillName ?? ""
            self.skillDescription = response.skillDescription ?? ""
            self.skillSteps = response.skillSteps ?? response.result ?? ""
            self.teach = true
        }
    }

    func saveSkill() {
        perform {
            _ = try await self.bridge.call("teach", ["host": self.host, "project": self.project,
                              "name": self.skillName, "description": self.skillDescription, "steps": self.skillSteps])
            try await self.loadSkills()
            self.finish()
            self.teach = false
            self.message = "已保存到项目技能目录。"
        }
    }

    func loadSkills() async throws {
        guard !project.isEmpty else { skills = []; return }
        skills = try await bridge.call("skills", ["host": host, "project": project]).skills ?? []
    }

    func pick(_ field: ReferenceWritableKeyPath<TwinModel, String>, directory: Bool) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = directory
        panel.canChooseFiles = !directory
        panel.prompt = "选择"
        if panel.runModal() == .OK, let url = panel.url { self[keyPath: field] = url.path }
    }
}

@main
struct TwinApplication {
    @MainActor static func main() {
        let application = NSApplication.shared
        let delegate = TwinDelegate()
        application.delegate = delegate
        application.setActivationPolicy(.accessory)
        withExtendedLifetime(delegate) { application.run() }
    }
}

private struct SpriteArt: View {
    @ObservedObject var model: TwinModel

    var body: some View {
        ZStack(alignment: .topTrailing) {
            BrandedPixelTwin(evolution: model.evolution, accent: model.avatarShade.color, traits: model.appearanceTraits)
            if model.recording {
                Rectangle().fill(TwinShade.highlight).frame(width: 7, height: 7).padding(7)
            } else if model.nextWork != nil {
                Rectangle().fill(TwinShade.light).frame(width: 5, height: 5).rotationEffect(.degrees(45)).padding(7)
            }
        }
        .frame(width: SpriteView.size.width, height: SpriteView.size.height)
        .allowsHitTesting(false)
    }
}

private final class SpriteArtwork: NSHostingView<SpriteArt> {
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}

@MainActor
private final class SpriteView: NSButton {
    static let size = NSSize(width: 112, height: 78)
    var onPress: (() -> Void)?

    init(model: TwinModel) {
        super.init(frame: NSRect(origin: .zero, size: Self.size))
        title = ""
        isBordered = false
        focusRingType = .none
        setAccessibilityLabel("KITE by Memoket · " + model.displayLanguage.text("展开操作"))
        let art = SpriteArtwork(rootView: SpriteArt(model: model))
        art.frame = bounds
        art.autoresizingMask = [.width, .height]
        addSubview(art)
        target = self
        action = #selector(pressed)
    }

    required init?(coder: NSCoder) { nil }
    @objc private func pressed() { onPress?() }
    override func accessibilityPerformPress() -> Bool { pressed(); return true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

@MainActor
private final class TwinControlsWindow: NSWindow {
    var onKeyLoss: (() -> Void)?
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }

    override func resignKey() {
        super.resignKey()
        onKeyLoss?()
    }
}

private struct TwinControls: View {
    @ObservedObject var model: TwinModel
    let onResize: (CGFloat) -> Void

    var body: some View {
        TwinView(model: model, onResize: onResize)
    }
}

@MainActor
private final class TwinDelegate: NSObject, NSApplicationDelegate {
    private let model = TwinModel()
    private var controls: TwinControlsWindow!
    private var controlContentHeight: CGFloat = 200
    private var controlsDock: ControlDock?
    private var sprite: SpriteView!
    private var panel: SpriteWindow!
    private var tray: NSStatusItem?
    private var polling: Task<Void, Never>?
    private var browserPolling: Task<Void, Never>?
    private let shortcut = DraftShortcut()
    private var activityPolling: Task<Void, Never>?
    private var activationObserver: NSObjectProtocol?
    private var bubble: TwinControlsWindow?
    private var bubbleTimeout: Task<Void, Never>?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let frame = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 800, height: 600)
        let size = SpriteView.size
        panel = SpriteWindow(contentRect: NSRect(x: frame.maxX - size.width - 30, y: frame.minY + 40,
            width: size.width, height: size.height), styleMask: [.borderless],
            backing: .buffered, defer: false)
        panel.title = "KITE Twin"
        panel.isOpaque = false
        panel.ignoresMouseEvents = false
        panel.isMovable = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .ignoresCycle]

        sprite = SpriteView(model: model)
        sprite.onPress = { [weak self] in self?.toggle() }
        panel.onPress = sprite.onPress
        panel.onMove = { [weak self] point, cursor in
            self?.hideControls()
            self?.dismissBubble()
            self?.moveSprite(to: point, near: cursor)
        }
        panel.onMoveFinished = { [weak self] in
            guard let origin = self?.panel.frame.origin else { return }
            UserDefaults.standard.set([origin.x, origin.y], forKey: "sprite-origin")
        }
        sprite.menu = menu()
        panel.contentView = sprite
        if let saved = UserDefaults.standard.array(forKey: "sprite-origin") as? [Double], saved.count == 2 {
            let point = NSPoint(x: saved[0], y: saved[1])
            moveSprite(to: point, near: point)
        }
        panel.orderFrontRegardless()

        controls = TwinControlsWindow(contentRect: NSRect(x: 0, y: 0, width: TwinView.panelWidth, height: 200),
                                      styleMask: .borderless, backing: .buffered, defer: false)
        controls.title = "KITE Twin 操作"
        controls.isOpaque = false
        controls.backgroundColor = .clear
        controls.hasShadow = true
        controls.isReleasedWhenClosed = false
        controls.level = .floating
        controls.onKeyLoss = { [weak self] in
            guard let self, self.model.quickMenuVisible else { return }
            self.hideControls()
        }
        let controller = NSHostingController(rootView: TwinControls(model: model) { [weak self] height in
            self?.controlContentHeight = height
            if self?.controls.isVisible == true { self?.positionControls() }
        })
        controls.contentViewController = controller
        let tray = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        tray.button?.image = NSImage(systemSymbolName: "sparkle", accessibilityDescription: "KITE Twin")
        tray.menu = menu()
        self.tray = tray
        model.onDisplayLanguageChange = { [weak self] in
            guard let self else { return }
            self.sprite.menu = self.menu()
            self.sprite.setAccessibilityLabel("KITE by Memoket · " + self.model.displayLanguage.text("展开操作"))
            self.tray?.menu = self.menu()
        }
        model.hideForCapture = { [weak self] in
            self?.controls.orderOut(nil)
            self?.panel.orderOut(nil)
        }
        model.showControls = { [weak self] in self?.showPanel() }
        model.dismissControls = { [weak self] in self?.hideControls() }
        model.onNewWork = { [weak self] quote in self?.showBubble(.memory(quote)) }
        shortcut.onTrigger = { [weak self] in
            guard let self, !self.model.working else { return }
            let front = NSWorkspace.shared.frontmostApplication
            self.model.beginReply(channel: self.model.activity.channel(for: front, allowBrowser: true), app: front)
            self.showPanel()
        }
        model.shortcutAvailable = shortcut.register()
        model.rememberForeground()
        activationObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { [weak self] notification in
            let active = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
            MainActor.assumeIsolated { self?.model.rememberForeground(active) }
        }
        NSApp.servicesProvider = self
        NSUpdateDynamicServices()
        activityPolling = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, !Task.isCancelled else { break }
                if self.controls.isVisible { self.model.checkEditorPermission() }
                if !self.controls.isVisible && !self.model.working && !self.model.recording,
                   self.bubble?.isVisible != true,
                   let (channel, target) = self.model.activity.hint(enabled: self.model.detectReplyEditors) {
                    self.showBubble(.reply(channel, target))
                }
            }
        }
        browserPolling = Task { [weak self] in
            while !Task.isCancelled {
                if let self, let request = BrowserReply.pending() {
                    self.model.beginBrowserReply(request)
                    if self.model.browserReply?.id == request.id { self.showPanel() }
                }
                try? await Task.sleep(for: .milliseconds(370))
            }
        }
        polling = Task { [model] in
            model.perform { try await model.refresh() }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(12))
                if !model.recording && !model.working && !model.settings && model.version != nil {
                    try? await model.refresh()
                }
            }
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        model.recording || model.working ? .terminateCancel : .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        polling?.cancel()
        browserPolling?.cancel()
        model.discardBrowserReply()
        activityPolling?.cancel()
        bubbleTimeout?.cancel()
        shortcut.stop()
        if let activationObserver { NSWorkspace.shared.notificationCenter.removeObserver(activationObserver) }
        model.discard()
    }

    @objc(kiteReply:userData:error:)
    func kiteReply(_ selection: NSPasteboard, userData: String?, error: AutoreleasingUnsafeMutablePointer<NSString?>?) {
        guard !model.working else { error?.pointee = "KITE 正在处理上一条消息。"; return }
        guard let selected = selection.string(forType: .string), !selected.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            error?.pointee = "先选中要回复的消息。"
            return
        }
        let origin = NSWorkspace.shared.frontmostApplication
        model.beginReply(channel: model.activity.channel(for: origin, allowBrowser: true), app: origin, suppliedText: selected)
        showPanel()
    }

    private func moveSprite(to origin: NSPoint, near pointer: NSPoint) {
        let screen = NSScreen.screens.first { $0.frame.contains(pointer) } ?? NSScreen.main
        guard let bounds = screen?.visibleFrame else { return }
        let size = panel.frame.size
        panel.setFrameOrigin(NSPoint(x: min(max(origin.x, bounds.minX), bounds.maxX - size.width),
                                     y: min(max(origin.y, bounds.minY), bounds.maxY - size.height)))
    }

    private func menu() -> NSMenu {
        let menu = NSMenu()
        let open = menu.addItem(withTitle: model.displayLanguage.text("打开 Twin"), action: #selector(showPanel), keyEquivalent: "")
        let quit = menu.addItem(withTitle: model.displayLanguage.text("退出 KITE Twin"), action: #selector(quit), keyEquivalent: "q")
        open.target = self
        quit.target = self
        return menu
    }

    @objc private func showPanel() {
        model.quickMenuVisible = false
        revealControls()
    }

    private func revealControls() {
        model.rememberForeground()
        model.checkEditorPermission()
        model.checkScreenPermission()
        if !controls.isVisible, let bubble {
            controlsDock = bubble.frame.midY >= panel.frame.midY ? .above : .below
        }
        dismissBubble()
        NSApp.activate()
        positionControls()
        controls.makeKeyAndOrderFront(nil)
    }

    private func hideControls() {
        let restoreSprite = model.quickMenuVisible
        model.quickMenuVisible = false
        controls.orderOut(nil)
        controlsDock = nil
        if restoreSprite { panel.orderFrontRegardless() }
    }

    private func dismissBubble() {
        bubbleTimeout?.cancel()
        bubbleTimeout = nil
        bubble?.orderOut(nil)
        bubble = nil
    }

    private func showBubble(_ hint: TwinHint) {
        guard !controls.isVisible, !model.recording, let screen = panel.screen ?? NSScreen.main else { return }
        dismissBubble()
        let view = TwinBubble(hint: hint, subject: model.nextWorkTitle, shortcutAvailable: model.shortcutAvailable,
                              language: model.displayLanguage) { [weak self] in
            guard let self else { return }
            switch hint {
            case .memory(let quote):
                self.showPanel()
                self.model.prepareWork(quote)
            case .reply(let channel, let target):
                self.model.beginReply(channel: channel, app: target)
                self.showPanel()
            }
        } onDismiss: { [weak self] in self?.dismissBubble() }
        let controller = NSHostingController(rootView: view)
        let size = controller.sizeThatFits(in: NSSize(width: 324, height: 380))
        let bounds = screen.visibleFrame
        let x = min(max(panel.frame.midX - size.width / 2, bounds.minX + 8), bounds.maxX - size.width - 8)
        let above = panel.frame.maxY + 8
        let y = above + size.height <= bounds.maxY - 8 ? above : max(bounds.minY + 8, panel.frame.minY - size.height - 8)
        let window = TwinControlsWindow(contentRect: NSRect(origin: NSPoint(x: x, y: y), size: size),
                                        styleMask: .borderless, backing: .buffered, defer: false)
        window.title = "KITE 提醒"
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.isReleasedWhenClosed = false
        window.level = .floating
        window.contentViewController = controller
        bubble = window
        window.orderFrontRegardless()
        if window.isVisible {
            Logger(subsystem: "org.memoket.kite.twin", category: "reminder").notice("An in-app reminder bubble is visible.")
        }
        bubbleTimeout = Task { [weak self] in
            try? await Task.sleep(for: .seconds(22))
            if !Task.isCancelled { self?.dismissBubble() }
        }
    }

    private func positionControls() {
        let center = NSPoint(x: panel.frame.midX, y: panel.frame.midY)
        let screen = panel.screen ?? NSScreen.screens.first { $0.frame.contains(center) } ?? NSScreen.main
        guard let bounds = screen?.visibleFrame else { return }
        controls.hasShadow = !model.quickMenuVisible
        if model.quickMenuVisible {
            let size = TwinView.wheelSize
            let x = min(max(center.x - size.width / 2, bounds.minX), bounds.maxX - size.width)
            let y = min(max(center.y - size.height / 2, bounds.minY), bounds.maxY - size.height)
            panel.orderOut(nil)
            controls.setFrame(NSRect(origin: NSPoint(x: x, y: y), size: size), display: controls.isVisible)
            return
        }
        if !panel.isVisible { panel.orderFrontRegardless() }
        let desired = min(max(160, controlContentHeight), 628, bounds.height - 24)
        let dock = controlsDock ?? ControlDock.choose(sprite: panel.frame, bounds: bounds, height: desired)
        controlsDock = dock
        let height = dock.clampHeight(desired, sprite: panel.frame, bounds: bounds)
        controls.setFrame(dock.frame(sprite: panel.frame, bounds: bounds,
                                     size: NSSize(width: TwinView.panelWidth, height: height)),
                          display: controls.isVisible)
    }

    private func toggle() {
        if controls.isVisible {
            hideControls()
        } else {
            model.quickMenuVisible = true
            revealControls()
        }
    }

    @objc private func quit() { NSApp.terminate(nil) }
}
