import AppKit
import CoreGraphics
import ScreenCaptureKit

@MainActor
enum TwinScreenCapture {
    static var isAuthorized: Bool { CGPreflightScreenCaptureAccess() }

    static func requestAccess() -> Bool {
        isAuthorized || CGRequestScreenCaptureAccess()
    }

    static func openSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") {
            NSWorkspace.shared.open(url)
        }
    }

    static func captureSelectedRegion() async throws -> CGImage? {
        guard isAuthorized else { throw permissionError }
        let selector = ScreenSelection()
        guard let selection = await selector.select() else { return nil }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            guard let display = content.displays.first(where: { $0.displayID == selection.displayID }) else {
                throw TwinError("选中的屏幕已断开，请重新截屏。")
            }
            let ownApps = content.applications.filter { $0.processID == ProcessInfo.processInfo.processIdentifier }
            let filter = SCContentFilter(display: display, excludingApplications: ownApps, exceptingWindows: [])
            let config = SCStreamConfiguration()
            config.sourceRect = selection.rect
            let scale = CGFloat(filter.pointPixelScale)
            config.width = Int((selection.rect.width * scale).rounded(.up))
            config.height = Int((selection.rect.height * scale).rounded(.up))
            config.showsCursor = false
            return try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        } catch {
            let failure = error as NSError
            if !isAuthorized || (failure.domain == SCStreamErrorDomain && failure.code == SCStreamError.Code.userDeclined.rawValue) {
                throw permissionError
            }
            throw TwinError("截屏失败。\(error.localizedDescription)")
        }
    }

    static var permissionError: TwinError {
        TwinError("KITE Twin 尚未取得截屏权限。请在「录屏与系统录音」中打开 KITE Twin。如果开关已打开，请退出并重新打开 Twin。")
    }
}

@MainActor
private final class ScreenSelection {
    struct Region {
        let displayID: CGDirectDisplayID
        let rect: CGRect
    }

    private var windows: [SelectionWindow] = []
    private var continuation: CheckedContinuation<Region?, Never>?

    func select() async -> Region? {
        await withCheckedContinuation { continuation in
            self.continuation = continuation
            for screen in NSScreen.screens {
                guard let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else {
                    continue
                }
                let displayID = number.uint32Value
                let window = SelectionWindow(contentRect: screen.frame, styleMask: .borderless, backing: .buffered, defer: false)
                window.title = "KITE Twin 截屏选区"
                window.level = .screenSaver
                window.isOpaque = false
                window.backgroundColor = .clear
                window.hasShadow = false
                window.isReleasedWhenClosed = false
                window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
                let view = SelectionView(frame: CGRect(origin: .zero, size: screen.frame.size))
                view.finish = { [weak self] rect in
                    self?.complete(rect.map { Region(displayID: displayID, rect: $0) })
                }
                window.contentView = view
                window.initialFirstResponder = view
                windows.append(window)
                window.orderFrontRegardless()
            }
            let pointer = NSEvent.mouseLocation
            if let active = windows.first(where: { $0.frame.contains(pointer) }) ?? windows.first {
                NSApp.activate()
                active.makeKeyAndOrderFront(nil)
                active.makeFirstResponder(active.contentView)
            } else {
                complete(nil)
            }
        }
    }

    private func complete(_ region: Region?) {
        guard let continuation else { return }
        self.continuation = nil
        for window in windows { window.orderOut(nil) }
        windows.removeAll()
        continuation.resume(returning: region)
    }
}

private final class SelectionWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

@MainActor
private final class SelectionView: NSView {
    var finish: ((CGRect?) -> Void)?
    private var start: CGPoint?
    private var selection = CGRect.zero

    override var isFlipped: Bool { true }
    override var acceptsFirstResponder: Bool { true }

    override init(frame: CGRect) {
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(.group)
        setAccessibilityLabel("拖动选择截屏区域，按 Escape 取消")

        let prompt = NSTextField(labelWithString: "拖动选择区域，松开后预览")
        let cancel = NSButton(title: "取消", target: self, action: #selector(cancelSelection))
        cancel.keyEquivalent = "\u{1b}"
        let bar = NSStackView(views: [prompt, cancel])
        bar.edgeInsets = NSEdgeInsets(top: 6, left: 11, bottom: 6, right: 8)
        bar.spacing = 10
        bar.appearance = NSAppearance(named: .darkAqua)
        bar.wantsLayer = true
        bar.layer?.backgroundColor = NSColor(white: 0.10, alpha: 0.96).cgColor
        bar.layer?.cornerRadius = 8
        bar.translatesAutoresizingMaskIntoConstraints = false
        addSubview(bar)
        NSLayoutConstraint.activate([
            bar.centerXAnchor.constraint(equalTo: centerXAnchor),
            bar.topAnchor.constraint(equalTo: topAnchor, constant: 24),
        ])
    }

    required init?(coder: NSCoder) { nil }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func resetCursorRects() { addCursorRect(bounds, cursor: .crosshair) }

    override func draw(_ dirtyRect: CGRect) {
        let shade = NSBezierPath(rect: bounds)
        shade.appendRect(selection)
        shade.windingRule = .evenOdd
        NSColor.black.withAlphaComponent(0.3).setFill()
        shade.fill()
        if !selection.isEmpty {
            NSColor.white.setStroke()
            NSBezierPath(rect: selection.insetBy(dx: 0.5, dy: 0.5)).stroke()
        }
    }

    override func mouseDown(with event: NSEvent) {
        start = convert(event.locationInWindow, from: nil)
        selection = .zero
        window?.makeFirstResponder(self)
    }

    override func mouseDragged(with event: NSEvent) {
        guard let start else { return }
        let point = convert(event.locationInWindow, from: nil)
        selection = CGRect(x: min(point.x, start.x), y: min(point.y, start.y),
                           width: abs(point.x - start.x), height: abs(point.y - start.y))
            .integral.intersection(bounds)
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        mouseDragged(with: event)
        if selection.width >= 2, selection.height >= 2 { finish?(selection) }
        else { start = nil; selection = .zero; needsDisplay = true }
    }

    override func rightMouseDown(with event: NSEvent) { finish?(nil) }
    override func cancelOperation(_ sender: Any?) { finish?(nil) }
    @objc private func cancelSelection() { finish?(nil) }
}
