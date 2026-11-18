import AppKit
import SwiftUI
import UniformTypeIdentifiers

private let ink = Color(red: 0.059, green: 0.067, blue: 0.083)
private let panel = Color(red: 0.095, green: 0.110, blue: 0.128)
private let rule = Color.white.opacity(0.13)
private let secondaryText = Color.white.opacity(0.70)
private let lavender = TwinShade.light

enum TwinShade: String, CaseIterable {
    case iris, lilac, orchid

    static let highlight = Color(red: 0.93, green: 0.86, blue: 1)
    static let light = Color(red: 0.79, green: 0.65, blue: 0.99)
    static let shadow = Color(red: 0.43, green: 0.25, blue: 0.71)
    static let ink = Color(red: 0.16, green: 0.08, blue: 0.30)

    var title: String {
        switch self {
        case .iris: "鸢尾紫"
        case .lilac: "淡紫"
        case .orchid: "兰花紫"
        }
    }

    var color: Color {
        switch self {
        case .iris: Color(red: 0.65, green: 0.43, blue: 0.94)
        case .lilac: Color(red: 0.78, green: 0.60, blue: 0.99)
        case .orchid: Color(red: 0.71, green: 0.40, blue: 0.82)
        }
    }
}

struct TwinWordmark: View {
    private static let title = "KITE by Memoket"
    private static let width: CGFloat = 110
    private static let height: CGFloat = 30
    private static let glyphs: [Character: [String]] = [
        "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
        "I": ["111", "010", "010", "010", "010", "010", "111"],
        "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
        "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
        "b": ["10000", "10000", "11110", "10001", "10001", "10001", "11110"],
        "y": ["00000", "00000", "10001", "10001", "01111", "00001", "11110"],
        "M": ["10001", "11011", "10101", "10001", "10001", "10001", "10001"],
        "e": ["00000", "00000", "01110", "10001", "11111", "10000", "01111"],
        "m": ["00000", "00000", "11010", "10101", "10101", "10101", "10101"],
        "o": ["00000", "00000", "01110", "10001", "10001", "10001", "01110"],
        "k": ["1000", "1000", "1001", "1010", "1100", "1010", "1001"],
        "t": ["0100", "0100", "1110", "0100", "0100", "0101", "0010"],
    ]
    private static let pixels: (brand: Path, byline: Path) = {
        var brand = Path()
        var byline = Path()
        let advances = title.map { letter in glyphs[letter].map { $0[0].count + 1 } ?? 3 }
        let middle = CGFloat(advances.reduce(0, +)) / 2
        let pixel: CGFloat = 1.12
        let radius: CGFloat = 47
        var cursor: CGFloat = 0
        for (index, letter) in title.enumerated() {
            let advance = CGFloat(advances[index])
            defer { cursor += advance }
            guard let rows = glyphs[letter] else { continue }
            let glyphWidth = CGFloat(rows[0].count)
            let angle = (cursor + glyphWidth / 2 - middle) * pixel / radius
            let x = width / 2 + radius * sin(angle)
            let y = height - 6 - radius * (1 - cos(angle))
            let transform = CGAffineTransform(translationX: x, y: y)
                .rotated(by: -angle).scaledBy(x: pixel, y: pixel)
            var glyph = Path()
            for (y, row) in rows.enumerated() {
                for (column, value) in row.enumerated() where value == "1" {
                    glyph.addRect(CGRect(x: CGFloat(column) - glyphWidth / 2, y: CGFloat(y) - 3.5, width: 1, height: 1))
                }
            }
            if index < 4 { brand.addPath(glyph, transform: transform) }
            else { byline.addPath(glyph, transform: transform) }
        }
        return (brand, byline)
    }()

    var body: some View {
        Canvas { context, _ in
            let pixels = Self.pixels
            let shadow = CGAffineTransform(translationX: 1, y: 1)
            context.fill(pixels.brand.applying(shadow), with: .color(TwinShade.ink))
            context.fill(pixels.byline.applying(shadow), with: .color(TwinShade.ink))
            context.fill(pixels.brand, with: .color(TwinShade.iris.color))
            context.fill(pixels.byline, with: .color(TwinShade.highlight))
        }
        .frame(width: Self.width, height: Self.height)
        .accessibilityLabel(Self.title)
    }
}

struct BrandedPixelTwin: View {
    let evolution: Int
    let accent: Color
    let traits: Set<String>

    var body: some View {
        ZStack(alignment: .bottom) {
            PixelTwin(evolution: evolution, accent: accent, traits: traits)
                .frame(width: 64, height: 58)
                .frame(maxHeight: .infinity, alignment: .top)
            TwinWordmark().padding(.bottom, 3)
        }
        .frame(width: 112, height: 78)
    }
}

struct PixelTwin: View {
    let evolution: Int
    let accent: Color
    var traits: Set<String> = []
    @State private var floating = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Canvas { context, size in
            let pixel = floor(min(size.width, size.height) / 18)
            let origin = (size.width - 18 * pixel) / 2
            func paint(_ x: Int, _ y: Int, _ w: Int, _ h: Int, _ color: Color) {
                context.fill(Path(CGRect(x: origin + CGFloat(x) * pixel, y: CGFloat(y) * pixel,
                    width: CGFloat(w) * pixel, height: CGFloat(h) * pixel)), with: .color(color))
            }
            if evolution < 2 {
                paint(6, 1, 6, 2, TwinShade.highlight)
                paint(4, 3, 10, 2, TwinShade.light)
                paint(3, 5, 12, 8, accent)
                paint(4, 13, 10, 2, TwinShade.shadow)
                paint(6, 15, 6, 1, TwinShade.shadow)
                paint(5, 6, 3, 2, TwinShade.highlight)
                paint(5, 8, 2, 1, TwinShade.light)
                if evolution == 1 {
                    paint(8, 6, 2, 2, TwinShade.ink); paint(10, 8, 2, 2, TwinShade.ink); paint(8, 10, 2, 1, TwinShade.ink)
                    paint(12, 2, 1, 1, accent)
                }
            } else {
                paint(5, 3, 8, 2, accent)
                paint(3, 5, 12, 7, accent)
                paint(5, 12, 8, 3, TwinShade.shadow)
                paint(4, 15, 3, 1, accent); paint(11, 15, 3, 1, accent)
                paint(6, 8, 2, 2, TwinShade.ink); paint(10, 8, 2, 2, TwinShade.ink); paint(8, 11, 2, 1, TwinShade.ink)
                if traits.contains("background") {
                    paint(7, 1, 4, 1, accent); paint(8, 0, 2, 1, TwinShade.highlight)
                }
                if traits.contains("work_habits") {
                    paint(7, 13, 4, 1, TwinShade.highlight)
                }
                if traits.contains("communication") {
                    paint(15, 5, 2, 2, TwinShade.light)
                }
                if traits.contains("preferences") {
                    paint(3, 6, 2, 2, TwinShade.light)
                }
                if evolution == 3 {
                    paint(1, 8, 2, 2, TwinShade.light); paint(15, 8, 2, 2, TwinShade.light)
                    paint(8, 0, 2, 2, TwinShade.highlight); paint(14, 2, 1, 1, TwinShade.highlight)
                }
            }
        }
        .offset(y: reduceMotion ? 0 : floating ? -3 : 3)
        .onAppear { withAnimation(.easeInOut(duration: 1.9).repeatForever(autoreverses: true)) { floating = true } }
        .accessibilityHidden(true)
    }
}

enum TwinSettingsSection: String, CaseIterable {
    case reminders = "提醒与权限"
    case workspace = "工作区"
    case capture = "采集与外观"
    case shortcuts = "快捷键"
}

private struct TwinTileStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(configuration.isPressed ? .white.opacity(0.13) : panel, in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(rule))
            .opacity(enabled ? 1 : 0.45)
    }
}

private struct WheelPosition: GeometryEffect {
    static let radius = 103.0
    let slot: Int
    var progress: Double

    static func point(slot: Int, progress: Double) -> CGPoint {
        let angle = (Double(slot) * 60 - 90 + 360 * progress) * .pi / 180
        let distance = 0.52 + progress * 0.48
        return CGPoint(x: radius * cos(angle) * distance, y: radius * sin(angle) * distance)
    }

    var animatableData: Double {
        get { progress }
        set { progress = newValue }
    }

    func effectValue(size: CGSize) -> ProjectionTransform {
        let point = Self.point(slot: slot, progress: progress)
        return ProjectionTransform(CGAffineTransform(translationX: point.x, y: point.y))
    }
}

private struct WheelActionLabel: View {
    let title: String
    let symbol: String
    @Environment(\.isEnabled) private var enabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var hovering = false

    var body: some View {
        VStack(spacing: 4) {
            Image(systemName: symbol)
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(hovering ? ink : lavender)
                .frame(width: 42, height: 42)
                .background(hovering ? lavender : ink.opacity(0.96), in: Circle())
                .overlay(Circle().strokeBorder(hovering ? lavender : lavender.opacity(0.35)))
                .shadow(color: .black.opacity(0.30), radius: 6, y: 2)
                .accessibilityHidden(true)
            Text(title)
                .font(.system(size: 10, weight: .medium))
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .frame(maxWidth: 88)
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(ink.opacity(0.92), in: Capsule())
        }
        .fixedSize()
        .contentShape(.rect)
        .opacity(enabled ? 1 : 0.52)
        .onHover { hovering = enabled && $0 }
        .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: hovering)
    }
}

private struct TwinActionRow<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8, content: content)
            VStack(alignment: .leading, spacing: 8, content: content)
        }
    }
}

struct TwinView: View {
    static let panelWidth: CGFloat = 452
    static let wheelSize = CGSize(width: 318, height: 308)
    private static let headerHeight: CGFloat = 62
    private static let tabHeight: CGFloat = 46

    @ObservedObject var model: TwinModel
    let onResize: (CGFloat) -> Void
    @State private var isTargeted = false
    @State private var wheelProgress = 0.0
    @State private var wheelReady = false
    @State private var editingProfile: String?
    @State private var profileDraft = ""
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var isOverview: Bool { model.page == "现在" }
    private var isCapturePage: Bool { model.page == "记忆" || model.page == "Memoket" }
    private func ui(_ text: String) -> String { model.displayLanguage.text(text) }
    private var pageTitle: String {
        switch model.page {
        case "记忆": "记忆"
        case "画像": "我的画像"
        case "Memoket": "从 Memoket 导入"
        case "行动": "任务"
        case "回复": "回复"
        case "技能": "项目技能"
        default: "总览"
        }
    }

    var body: some View {
        Group {
            if model.quickMenuVisible { quickMenu }
            else { detailedPanel }
        }
        .foregroundStyle(.white)
        .preferredColorScheme(.dark)
        .dropDestination(for: URL.self) { urls, _ in
            guard let url = urls.first, url.isFileURL, canStartCapture else { return false }
            go("记忆")
            model.load(url)
            return true
        } isTargeted: { isTargeted = $0 }
        .onExitCommand { model.dismissControls?() }
        .sheet(isPresented: $model.settings) { settings }
        .sheet(isPresented: $model.teach) { teach }
    }

    private var detailedPanel: some View {
        let contentInset: CGFloat = 18
        return VStack(spacing: 0) {
            header
            navigation
            Rectangle().fill(rule).frame(height: 1)
            ScrollViewReader { reader in
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if isCapturePage { feedback.id("capture-status") }
                        content
                        if !isCapturePage { feedback }
                    }
                    .frame(width: Self.panelWidth - 2 * contentInset, alignment: .leading)
                    .padding(contentInset)
                    .background {
                        GeometryReader { geometry in
                            Color.clear.onChange(of: geometry.size.height, initial: true) { _, height in
                                let chrome = Self.headerHeight + Self.tabHeight + 1
                                let measuredPage = model.page
                                DispatchQueue.main.async {
                                    guard !model.quickMenuVisible, model.page == measuredPage else { return }
                                    onResize(height + chrome)
                                }
                            }
                        }
                    }
                }
                .id(model.page == "记忆" && model.pending != nil ? "记忆预览" : model.page)
                .scrollBounceBehavior(.basedOnSize)
                .onChange(of: model.operationFailed) { _, failed in
                    if failed && isCapturePage { reader.scrollTo("capture-status", anchor: .top) }
                }
            }
        }
        .frame(width: Self.panelWidth)
        .background(ink)
        .clipShape(.rect(cornerRadius: 16))
        .overlay {
            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(isTargeted ? lavender : rule, lineWidth: isTargeted ? 2 : 1)
                .allowsHitTesting(false)
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            RoundedRectangle(cornerRadius: 3)
                .fill(lavender)
                .frame(width: 11, height: 11)
                .rotationEffect(.degrees(45))
                .frame(width: 32, height: 32)
                .background(lavender.opacity(0.10), in: .rect(cornerRadius: 9))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text("KITE Twin").font(.system(size: 16, weight: .semibold))
                Text(ui(pageTitle))
                    .font(.system(size: 11, weight: .medium)).foregroundStyle(secondaryText)
            }
            Spacer(minLength: 8)
            if model.recording {
                Button {
                    go("记忆")
                    model.toggleRecording()
                } label: {
                    Label(ui("停止录音"), systemImage: "stop.circle.fill")
                }
                .tint(.red).disabled(model.working)
            }
            headerButton("设置") { model.openSettings(.reminders) }
            headerButton("收起") { model.dismissControls?() }
        }
        .padding(.horizontal, 17)
        .frame(height: Self.headerHeight)
    }

    private func headerButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(ui(title))
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(secondaryText)
                .padding(.horizontal, 10)
                .frame(height: 29)
                .background(.white.opacity(0.06), in: .rect(cornerRadius: 7))
        }
        .buttonStyle(.borderless)
    }

    private var navigation: some View {
        HStack(spacing: 5) {
            tab("总览", symbol: "square.grid.2x2", page: "现在", key: "1")
            tab("记忆", symbol: "tray", page: "记忆", key: "2")
            tab("任务", symbol: "checkmark.circle", page: "行动", key: "3")
            tab("回复", symbol: "arrowshape.turn.up.left", page: "回复", key: "4")
        }
        .padding(.horizontal, 14)
        .frame(height: Self.tabHeight)
    }

    private func tab(_ title: String, symbol: String, page: String, key: KeyEquivalent) -> some View {
        let selected = model.page == page || (page == "行动" && model.page == "技能")
            || (page == "记忆" && ["Memoket", "画像"].contains(model.page))
        return Button { go(page) } label: {
            Label(ui(title), systemImage: symbol)
                .font(.system(size: 12, weight: selected ? .semibold : .medium))
                .foregroundStyle(selected ? lavender : secondaryText)
                .frame(maxWidth: .infinity).frame(height: 32)
                .background(selected ? lavender.opacity(0.10) : .clear, in: .rect(cornerRadius: 8))
        }
        .buttonStyle(.borderless)
        .keyboardShortcut(key, modifiers: .command)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func go(_ page: String) {
        model.quickMenuVisible = false
        model.page = page
        if page == "技能" {
            model.perform(status: "正在读取项目技能…") {
                try await model.loadSkills()
                model.message = ""
            }
        }
        if page == "Memoket", model.memoketRecordings == nil, MemoketCLI.executable != nil {
            model.loadMemoketRecordings()
        }
    }

    private var quickMenu: some View {
        ZStack {
            Circle()
                .strokeBorder(lavender.opacity(0.46), style: StrokeStyle(lineWidth: 1, dash: [1, 4]))
                .frame(width: WheelPosition.radius * 2, height: WheelPosition.radius * 2)
                .shadow(color: .black.opacity(0.45), radius: 1)
                .opacity(wheelProgress * 0.8)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
            Button { model.dismissControls?() } label: {
                ZStack {
                    Circle()
                        .fill(RadialGradient(colors: [TwinShade.light.opacity(0.20), .clear], center: .center,
                                             startRadius: 2, endRadius: 42))
                        .frame(width: 84, height: 84)
                        .accessibilityHidden(true)
                    BrandedPixelTwin(evolution: model.evolution, accent: model.avatarShade.color, traits: model.appearanceTraits)
                }
                .contentShape(.rect)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(ui("收起转盘"))
            .help(ui("再点小人收起转盘"))
            Button { go("现在") } label: {
                Label(ui("总览"), systemImage: "square.grid.2x2")
                    .font(.system(size: 10, weight: .semibold))
                    .padding(.horizontal, 9).padding(.vertical, 4)
                    .background(ink.opacity(0.95), in: Capsule())
            }
            .buttonStyle(.plain)
            .offset(y: 51)
            .keyboardShortcut("1", modifiers: .command)
            ringAction(model.recording ? "停止录音" : "录音", symbol: model.recording ? "stop.circle" : "mic", slot: 0) {
                go("记忆")
                model.toggleRecording()
            }
            .disabled(model.working || model.pending != nil)
            .keyboardShortcut("r", modifiers: [.command, .shift])

            ringAction("截屏", symbol: "viewfinder", slot: 1) { go("记忆"); model.screenshot() }
                .disabled(!canStartCapture)
                .keyboardShortcut("s", modifiers: [.command, .shift])
            ringAction("导入文件", symbol: "doc.badge.plus", slot: 2) { go("记忆"); model.selectFile() }
                .disabled(!canStartCapture)
                .keyboardShortcut("o", modifiers: .command)
            ringAction("回复", symbol: "arrowshape.turn.up.left", slot: 3) { go("回复") }
                .keyboardShortcut("4", modifiers: .command)
            ringAction("任务", symbol: "checkmark.circle", slot: 4) { go("行动") }
                .keyboardShortcut("3", modifiers: .command)
            ringAction("技能", symbol: "square.stack", slot: 5) { go("技能") }
        }
        .frame(width: Self.wheelSize.width, height: Self.wheelSize.height)
        .onAppear {
            if reduceMotion {
                wheelProgress = 1
                wheelReady = true
            } else {
                withAnimation(.easeOut(duration: 0.52)) {
                    wheelProgress = 1
                } completion: {
                    if model.quickMenuVisible { wheelReady = true }
                }
            }
        }
        .onDisappear {
            wheelProgress = 0
            wheelReady = false
        }
    }

    @ViewBuilder
    private func ringAction(_ title: String, symbol: String, slot: Int, action: @escaping () -> Void) -> some View {
        let node = Button(action: action) {
            WheelActionLabel(title: ui(title), symbol: symbol)
        }.buttonStyle(.plain)
        if wheelReady {
            let point = WheelPosition.point(slot: slot, progress: 1)
            node.position(x: Self.wheelSize.width / 2 + point.x, y: Self.wheelSize.height / 2 + point.y + 10)
        } else {
            node
                .offset(y: 10)
                .modifier(WheelPosition(slot: slot, progress: wheelProgress))
                .opacity(min(1, wheelProgress * 2.8))
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
    }

    @ViewBuilder private var content: some View {
        switch model.page {
        case "记忆": capture
        case "画像": profilePage
        case "Memoket": memoketImport
        case "行动": task
        case "回复": replyEditor
        case "技能": skillList
        default: overview
        }
    }

    private func heading(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(ui(title)).font(.system(size: 23, weight: .semibold)).accessibilityAddTraits(.isHeader)
            Text(ui(detail)).font(.system(size: 12)).foregroundStyle(secondaryText)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var feedback: some View {
        if model.working || (!model.message.isEmpty && !model.settings) {
            HStack(alignment: .top, spacing: 8) {
                if model.working { ProgressView().controlSize(.small).tint(lavender) }
                else { Image(systemName: model.operationFailed ? "exclamationmark.circle" : "info.circle") }
                VStack(alignment: .leading, spacing: 6) {
                    Text(ui(model.message.isEmpty ? "处理中…" : model.message)).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    if model.screenPermissionNeeded && !model.working && model.page == "记忆" {
                        Button(ui("打开录屏权限设置")) { model.openScreenPermissionSettings() }
                    }
                }
            }
            .font(.system(size: 12))
            .foregroundStyle(model.operationFailed && !model.working ? Color.orange : secondaryText)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(11)
            .background(panel, in: .rect(cornerRadius: 9))
        }
    }

    private var growth: (title: String, next: String, page: String) {
        if model.version == nil { return ("正在连接记忆库", "连接后会显示小人的成长进度。", "记忆") }
        switch model.evolution {
        case 0: return ("从第一份记忆开始", "导入一份资料，小人的蛋壳会裂开。", "记忆")
        case 1: return ("小人开始成长", "再添加一份资料，就能孵化小人。", "记忆")
        case 2: return ("小人已孵化", "完成并确认一项任务，小人会获得星光。", "行动")
        default: return ("小人获得星光", "在记忆库里查看资料，或继续用原文推进任务。", "记忆")
        }
    }

    private var overview: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 15) {
                VStack(alignment: .leading, spacing: 7) {
                    Text(model.version == nil ? ui("记忆库") : model.displayLanguage == .english
                         ? "\(model.sourceCount) memory sources" : "\(model.sourceCount) 份记忆来源")
                        .font(.system(size: 11, weight: .semibold)).foregroundStyle(lavender)
                    Text(ui(growth.title)).font(.system(size: 24, weight: .semibold)).accessibilityAddTraits(.isHeader)
                    Text(ui(growth.next)).font(.system(size: 12)).foregroundStyle(secondaryText)
                        .fixedSize(horizontal: false, vertical: true)
                    TwinActionRow {
                        Button(ui(model.evolution == 2 ? "打开任务" : "打开记忆")) { go(growth.page) }
                        Button(ui("我的画像")) { go("画像") }
                    }.tint(lavender).padding(.top, 3)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                ZStack {
                    Circle().strokeBorder(lavender.opacity(0.20)).frame(width: 103)
                    Circle().fill(lavender.opacity(0.06)).frame(width: 85)
                    PixelTwin(evolution: model.evolution, accent: model.avatarShade.color, traits: model.appearanceTraits).frame(width: 66, height: 66)
                }.accessibilityHidden(true)
            }
            .padding(17)
            .background {
                LinearGradient(colors: [Color(red: 0.16, green: 0.10, blue: 0.23), panel],
                               startPoint: .topTrailing, endPoint: .bottomLeading)
                    .clipShape(.rect(cornerRadius: 14))
            }
            .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(rule).allowsHitTesting(false))

            if let quote = model.nextWork {
                card {
                    Text(ui("待跟进")).font(.system(size: 11, weight: .semibold)).foregroundStyle(lavender)
                    Text(model.nextWorkTitle).font(.headline).accessibilityAddTraits(.isHeader)
                    Text(quote.text).font(.subheadline).lineLimit(3)
                    Text(model.nextWorkTask).font(.system(size: 12)).foregroundStyle(secondaryText).lineLimit(2)
                    Button(ui("核对原文和任务")) { model.prepareWork(quote) }
                        .buttonStyle(.borderedProminent).tint(lavender)
                }
            } else {
                card {
                    Text(ui(model.configuredWatch ? "暂无新的跟进" : "设置一项关注"))
                        .font(.headline).accessibilityAddTraits(.isHeader)
                    Text(model.configuredWatch
                         ? (model.displayLanguage == .english
                            ? "Twin will notify you when new memory mentions “\(model.subject)”."
                            : "有新资料提到「\(model.subject)」时，小人会提示你。")
                         : ui("填写一个关注主题，例如「APP 外测」。新资料里有相关内容时，小人会提醒你。"))
                        .font(.system(size: 12)).foregroundStyle(secondaryText)
                    if !model.configuredWatch {
                        Button(ui("设置关注")) { model.openSettings(.reminders) }
                    }
                }
            }

            replySuggestions
        }
    }

    private var replySuggestions: some View {
        TwinActionRow {
            Label(ui(replyHintStatus), systemImage: "bell")
                .foregroundStyle(secondaryText)
            Button(ui("提醒设置")) { model.openSettings(.reminders) }
        }.font(.system(size: 12))
    }

    private var replyHintStatus: String {
        if !model.detectReplyEditors { return "回复提示已关闭" }
        return model.editorPermissionGranted ? "回复提示已启用" : "回复提示等待辅助功能授权"
    }

    private var canStartCapture: Bool { !model.working && !model.recording && model.pending == nil }

    @ViewBuilder private var capture: some View {
        if let pending = model.pending { preview(pending) }
        else { captureLibrary }
    }

    private var captureLibrary: some View {
        VStack(alignment: .leading, spacing: 14) {
            captureRow("我的画像", detail: "查看或修改从记忆自动生成的画像。",
                       symbol: "person.text.rectangle", shortcut: "") { go("画像") }

            heading("添加记忆", "先预览内容，再决定是否保存到当前工作区。")
            captureRow("导入文件", detail: "选文档或录音文件。也可以把文件拖到面板。", symbol: "doc.badge.plus", shortcut: "⌘O") {
                model.selectFile()
            }.disabled(!canStartCapture).keyboardShortcut("o", modifiers: .command)
            captureRow("截屏", detail: "拖出截图范围，预览图片和识别出的文字。", symbol: "viewfinder", shortcut: "⇧⌘S") {
                model.screenshot()
            }.disabled(!canStartCapture).keyboardShortcut("s", modifiers: [.command, .shift])
            captureRow(model.recording ? "停止并预览" : "录制会议",
                       detail: model.recording ? "录音正在进行，停止后可播放或转写。" : "录制麦克风与系统音频，结束后可在本机转写。",
                       symbol: model.recording ? "stop.circle" : "mic", shortcut: "⇧⌘R") {
                model.toggleRecording()
            }.disabled(model.working).keyboardShortcut("r", modifiers: [.command, .shift])
            captureRow("从 Memoket 导入", detail: "连接 Memoket CLI，选择一份已转写的录音。",
                       symbol: "link", shortcut: "") { go("Memoket") }
                .disabled(!canStartCapture)

            Divider()
            HStack {
                Text(ui("已存资料")).font(.headline).accessibilityAddTraits(.isHeader)
                Spacer()
                Button(ui("打开记忆图")) {
                    model.perform(status: "正在打开记忆图…") {
                        try await model.bridge.graph(language: model.displayLanguage)
                    }
                }
                    .disabled(model.working)
            }
            if model.items.isEmpty {
                Text(ui(model.version == nil ? "正在读取工作区…" : "还没有保存资料。"))
                    .font(.system(size: 12)).foregroundStyle(secondaryText)
            }
            ForEach(model.items.prefix(8)) { item in
                Button { model.openOriginal(item) } label: {
                    HStack {
                        Label(item.title, systemImage: item.kind == "recording" ? "waveform" : "doc.text")
                            .lineLimit(1)
                        Spacer()
                        Image(systemName: "arrow.up.right").font(.caption).foregroundStyle(secondaryText)
                    }.padding(10)
                }
                .buttonStyle(TwinTileStyle())
                .help(ui("打开原件"))
                .disabled(model.working || item.original == nil)
            }
        }
    }

    private var profilePage: some View {
        VStack(alignment: .leading, spacing: 12) {
            heading("我的画像", "从你本人的记忆中自动提炼，用于起草回复和执行任务。")
            if model.profile.isEmpty {
                card {
                    Text(ui(model.version == nil ? "正在连接记忆库…" : "暂时没有足够的自述来生成画像。"))
                        .font(.caption).foregroundStyle(secondaryText)
                }
            }
            ForEach(model.profile) { entry in
                card {
                    Text(ui(entry.label) + (entry.edited == true ? " · " + ui("已修改") : ""))
                        .font(.headline)
                    if editingProfile == entry.field {
                        TextField(ui("修改画像摘要"), text: $profileDraft, axis: .vertical)
                            .lineLimit(2...4)
                        HStack {
                            Button(ui("保存修改")) {
                                model.editProfile(entry, summary: profileDraft)
                                editingProfile = nil
                            }.disabled(profileDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || profileDraft.count > 240)
                            Button(ui("取消")) { editingProfile = nil }
                        }.disabled(model.working)
                    } else {
                        Text(entry.summary).font(.callout).textSelection(.enabled)
                        HStack {
                            Button(ui("修改")) {
                                profileDraft = entry.summary
                                editingProfile = entry.field
                            }
                            if entry.edited == true {
                                Button(ui("恢复自动生成")) { model.editProfile(entry, summary: "") }
                            }
                        }.disabled(model.working)
                    }
                    if !entry.citation.isEmpty {
                        DisclosureGroup(ui("原文引用")) {
                            Text(entry.quote).font(.caption).textSelection(.enabled)
                            Text(entry.title + " · " + entry.citation)
                                .font(.system(size: 9, design: .monospaced))
                                .foregroundStyle(secondaryText).textSelection(.enabled)
                        }
                    }
                }
            }
        }.onDisappear { editingProfile = nil }
    }

    private func captureRow(_ title: String, detail: String, symbol: String, shortcut: String,
                            action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: symbol).font(.system(size: 16, weight: .medium))
                    .foregroundStyle(lavender).frame(width: 30).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(ui(title)).font(.system(size: 13, weight: .semibold))
                    Text(ui(detail)).font(.system(size: 11)).foregroundStyle(secondaryText)
                }
                Spacer(minLength: 6)
                if !shortcut.isEmpty {
                    Text(shortcut).font(.system(size: 10, weight: .medium, design: .monospaced))
                        .foregroundStyle(secondaryText)
                }
            }
            .padding(11).frame(minHeight: 51)
        }.buttonStyle(TwinTileStyle())
    }

    private var memoketImport: some View {
        VStack(alignment: .leading, spacing: 14) {
            heading("从 Memoket 导入", "选一条录音，核对转写原文，再保存到当前记忆库。")
            if let recordings = model.memoketRecordings {
                if recordings.isEmpty {
                    Text(ui("暂时没有可显示的录音。"))
                        .font(.system(size: 12)).foregroundStyle(secondaryText)
                }
                ForEach(recordings) { recording in
                    let date = recording.date?.formatted(date: .abbreviated, time: .shortened)
                    let title = recording.title.isEmpty ? "未命名录音" : recording.title
                    captureRow(title, detail: date ?? "打开转写预览", symbol: "waveform", shortcut: "") {
                        model.previewMemoketRecording(recording)
                    }.disabled(!canStartCapture)
                }
                if let offset = model.memoketNextOffset {
                    Button(ui("加载更多录音")) { model.loadMemoketRecordings(offset: offset) }.disabled(model.working)
                }
                TwinActionRow {
                    Button(ui("刷新录音列表")) { model.loadMemoketRecordings() }
                    Button(ui("在浏览器重新登录")) { model.signInToMemoket() }
                }.disabled(model.working)
            } else {
                card {
                    if MemoketCLI.executable == nil {
                        Text(ui("安装 Memoket CLI")).font(.headline)
                        Text(ui("安装后，用你的 Memoket 账号连接。"))
                            .font(.system(size: 12)).foregroundStyle(secondaryText)
                        Button(ui("安装官方 CLI")) { model.installMemoketCLI() }
                            .buttonStyle(.borderedProminent).tint(lavender)
                    } else {
                        Text(ui("连接 Memoket 账号")).font(.headline)
                        HStack {
                            Button(ui("在浏览器登录")) { model.signInToMemoket() }
                            Button(ui("读取录音")) { model.loadMemoketRecordings() }
                                .buttonStyle(.borderedProminent).tint(lavender)
                        }
                    }
                    Link(ui("Memoket CLI 帮助"), destination: MemoketCLI.docs).font(.caption)
                }.disabled(model.working)
            }
            Button(ui("返回记忆")) { go("记忆") }
        }
    }

    private func preview(_ pending: Capture) -> some View {
        let needsNote = pending.kind != "recording" && pending.transcript == nil && pending.segments.isEmpty
        let hasNote = !model.note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return VStack(alignment: .leading, spacing: 10) {
            heading("保存前预览", pending.title)
            if pending.memoketID != nil {
                Text(pending.memoketSelfSpeaker.map {
                    model.displayLanguage == .english ? "Memoket matched your speaker: \($0)" : "Memoket 已识别本人说话人：\($0)"
                } ?? ui("Memoket 未标记本人说话人"))
                    .font(.caption).foregroundStyle(secondaryText)
            }
            if pending.kind == "recording" {
                HStack {
                    Button(ui("播放录音")) { NSWorkspace.shared.open(pending.url) }
                    if pending.transcript == nil {
                        Button(ui("本机转写")) { model.transcribe() }.buttonStyle(.borderedProminent).tint(lavender)
                    }
                }
                if let transcript = pending.transcript {
                    excerpt(transcript.prefix(14).map(\.text).joined(separator: "\n"))
                } else {
                    Text(ui("转写后才能保存为可搜索的记忆。首次转写需要选择本机语音模型。"))
                        .font(.system(size: 12)).foregroundStyle(secondaryText)
                }
            } else {
                if pending.kind == "screenshot", let screenshot = NSImage(contentsOf: pending.url) {
                    Image(nsImage: screenshot).resizable().scaledToFit().frame(maxHeight: 128)
                        .accessibilityLabel(ui("截图预览"))
                }
                let text = pending.transcript?.prefix(14).map { turn in
                    turn.speaker.map { "\($0) · " + turn.text } ?? turn.text
                }.joined(separator: "\n") ?? pending.segments.prefix(14).compactMap { $0["text"] }.joined(separator: "\n")
                if text.isEmpty {
                    Text(ui("没有识别到文字。请写一句说明，让 KITE 能通过文字找到这份记忆。"))
                        .font(.system(size: 12)).foregroundStyle(secondaryText)
                } else {
                    excerpt(text)
                }
            }
            TextField(ui(needsNote ? "描述内容（必填）" : "备注（可选）"), text: $model.note)
            if hasNote {
                Toggle(ui("这条备注是我的自述，可更新画像"), isOn: $model.noteIsSelf)
                    .font(.system(size: 12))
            }
            Text(model.displayLanguage == .english
                 ? "\(model.hostName) CLI · \(model.indexingModel) will index the text and generate an English view. The original stays on this Mac."
                 : "\(model.hostName) CLI · \(model.indexingModel) 将读取文本，建立索引并生成英文视图。原件保存在本机。")
                .font(.caption).foregroundStyle(secondaryText)
            HStack {
                Button(ui("取消"), role: .cancel) { model.discard() }
                Spacer()
                Button(ui("构建记忆")) { model.remember() }.buttonStyle(.borderedProminent).tint(lavender)
                    .disabled((pending.kind == "recording" && pending.transcript == nil) || (needsNote && !hasNote))
            }.disabled(model.working)
        }
    }

    private var task: some View {
        VStack(alignment: .leading, spacing: 14) {
            heading("根据记忆推进任务", String(format: ui("先核对引用和任务，然后交给 %@ 执行。"), model.hostName))
            card {
                Text(ui("查找任务相关原文")).font(.headline)
                HStack {
                    TextField(ui("主题，例如「APP 外测」"), text: $model.subject).onSubmit(findEvidence)
                    Button(ui("查原文"), action: findEvidence)
                        .disabled(model.subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }.disabled(model.working)
                if model.outstanding.isEmpty {
                    Text(ui("暂无待跟进的原文。你也可以直接说明要做什么。"))
                        .font(.system(size: 12)).foregroundStyle(secondaryText)
                }
                ForEach(model.outstanding.prefix(4)) { quote in
                    Button { model.selectQuote(quote) } label: {
                        HStack(alignment: .top) {
                            Image(systemName: model.selectedQuote?.id == quote.id ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(lavender).accessibilityHidden(true)
                            Text(quote.text).lineLimit(2).multilineTextAlignment(.leading)
                            Spacer(minLength: 0)
                        }
                        .font(.system(size: 12))
                        .padding(.vertical, 4)
                    }.buttonStyle(.plain)
                    .accessibilityAddTraits(model.selectedQuote?.id == quote.id ? .isSelected : [])
                }
                if let quote = model.selectedQuote {
                    DisclosureGroup(ui("核对已选原文")) {
                        excerpt(quote.text)
                        Text(quote.citation).font(.system(size: 10, design: .monospaced)).foregroundStyle(secondaryText)
                    }.font(.system(size: 12))
                }
            }
            card {
                Text(ui("要做什么？")).font(.headline)
                TextField(ui("例如：按最新决定更新发布计划，并运行检查。"), text: $model.task, axis: .vertical)
                    .lineLimit(2...4)
                HStack {
                    Button(ui(model.project.isEmpty ? "选择项目" : "更改项目")) { model.openSettings(.workspace) }
                    Spacer()
                    Button(model.displayLanguage == .english ? "Open in \(model.hostName)" : "在 \(model.hostName) 打开") { model.runTask() }
                        .buttonStyle(.borderedProminent).tint(lavender)
                        .disabled(model.working || model.task.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            if model.handedOff {
                card {
                    Text(ui("确认任务结果")).font(.headline)
                    Text(model.displayLanguage == .english
                         ? "Return with the reviewed result after completing the task in \(model.hostName)."
                         : "在 \(model.hostName) 完成任务后，把你核对过的结果带回来。")
                        .font(.system(size: 12)).foregroundStyle(secondaryText)
                    textEditor($model.result, label: "已核对的任务结果", height: 86)
                    HStack {
                        Button(ui("确认已完成")) { model.finish() }.disabled(model.result.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        Spacer()
                        Button(ui("保存为项目技能")) { model.suggestSkill() }
                            .disabled(model.working || model.result.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
            }
            Button { go("技能") } label: { Label(ui("查看项目技能"), systemImage: "square.stack") }
        }
    }

    private func findEvidence() {
        guard !model.subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        model.perform(status: "正在查找原文…") {
            try await model.scan()
            model.message = model.outstanding.isEmpty ? "没有找到匹配的待跟进原文。" : ""
        }
    }

    private var skillList: some View {
        VStack(alignment: .leading, spacing: 14) {
            heading("项目技能", "把一次任务中核对过的做法保存到项目，下次可以直接复用。")
            if model.project.isEmpty {
                card {
                    Text(ui("先选一个项目")).font(.headline)
                    Text(ui("KITE 会从这个项目读取技能。")).font(.system(size: 12)).foregroundStyle(secondaryText)
                    Button(ui("选择项目")) { model.openSettings(.workspace) }.buttonStyle(.borderedProminent).tint(lavender)
                }
            } else if model.skills.isEmpty {
                card {
                    Text(ui(model.working ? "正在读取技能" : "这个项目还没有技能")).font(.headline)
                    Text(ui("完成一项任务后，在任务页带回结果，再选「保存为项目技能」。"))
                        .font(.system(size: 12)).foregroundStyle(secondaryText)
                    Button(ui("打开任务")) { go("行动") }
                }
            } else {
                ForEach(model.skills) { skill in
                    card {
                        Text(skill.name).font(.headline)
                        if let description = skill.description, !description.isEmpty {
                            Text(description).font(.system(size: 12)).foregroundStyle(secondaryText).lineLimit(3)
                        }
                        HStack {
                            Button(ui("查看文件")) { NSWorkspace.shared.open(URL(fileURLWithPath: skill.path)) }
                            Spacer()
                            Button(ui("用于任务")) {
                                model.selectedQuote = nil
                                model.task = skill.instructions
                                go("行动")
                            }.buttonStyle(.borderedProminent).tint(lavender)
                        }
                    }
                }
            }
            Button(ui("返回任务")) { go("行动") }
        }
    }

    private var replyEditor: some View {
        VStack(alignment: .leading, spacing: 14) {
            if model.replyInput.isEmpty {
                heading("起草回复", "KITE 读取你选中的原消息，生成一份可编辑的草稿。")
                card {
                    if !model.editorPermissionGranted {
                        Text(ui("先允许读取选区")).font(.headline)
                        Text(ui("在 macOS 的「辅助功能」里允许 KITE Twin，然后回到原应用选中消息。"))
                            .font(.system(size: 12)).foregroundStyle(secondaryText)
                        Button(ui("打开辅助功能设置")) { model.requestEditorPermission() }
                            .buttonStyle(.borderedProminent).tint(lavender)
                    } else {
                        Text(ui("在原消息上开始")).font(.headline)
                        Text(ui(model.shortcutAvailable
                             ? "飞书和 Slack 桌面端选中原消息，按 ⌃⌥R。Gmail、Slack 和飞书网页使用 KITE 浏览器扩展的快捷键 ⌃⇧R。"
                             : "桌面端可用「服务」菜单的「用 KITE 起草回复」，网页使用 KITE 浏览器扩展。"))
                            .font(.system(size: 12)).foregroundStyle(secondaryText)
                    }
                    if model.editorPermissionGranted {
                        Button(ui("从当前飞书回复起草")) { model.beginReplyInPreviousApp() }
                            .buttonStyle(.borderedProminent).tint(lavender).disabled(model.working)
                    }
                    Text(ui("网页用户：固定 KITE 浏览器扩展。打开原消息的回复、选择正文，然后按 ⌃⇧R 或点扩展图标。"))
                        .font(.system(size: 12)).foregroundStyle(secondaryText)
                    if model.replyTargetAvailable && model.browserReply == nil {
                        Button(ui("返回原应用")) { model.returnToReplyApp() }
                    }
                }
                replySuggestions
            } else {
                heading(String(format: ui("%@ 回复草稿"), model.replyChannel.rawValue), "核对草稿后，在原应用发送。")
                DisclosureGroup(ui("查看选中的原消息")) { excerpt(model.replyInput) }
                if !model.replyDraft.isEmpty {
                    textEditor($model.replyDraft, label: "回复草稿", height: 118)
                    TwinActionRow {
                        if model.canFillReply { Button(ui("填入原应用回复框")) { model.fillReply() }.buttonStyle(.borderedProminent).tint(lavender) }
                        Button(ui("复制草稿")) { model.copyReply() }
                            .tint(lavender)
                    }.disabled(model.working)
                    if model.canDragReply {
                        Label(ui("拖入飞书原回复框"), systemImage: "hand.draw")
                            .font(.system(size: 12, weight: .semibold))
                            .padding(.horizontal, 12).padding(.vertical, 8)
                            .background(lavender.opacity(0.12), in: .rect(cornerRadius: 8))
                            .foregroundStyle(lavender)
                            .onDrag { model.replyDragProvider() }
                            .help(ui("把这块拖到飞书当前引用下面的输入区。"))
                            .allowsHitTesting(!model.working)
                    }
                    if model.browserReply != nil {
                        Button(ui("结束这条网页回复")) { model.discardBrowserReply() }.disabled(model.working)
                    } else if !model.canFillReply && !model.canDragReply {
                        Text(ui("没有绑定到原消息的回复框。请先在原应用打开这条消息的回复，再从原消息起草。"))
                            .font(.system(size: 12)).foregroundStyle(secondaryText)
                    }
                } else if !model.working {
                    Button(ui("重新起草")) { model.draftReply() }.buttonStyle(.borderedProminent).tint(lavender)
                }
                DisclosureGroup(ui("起草选项")) {
                    VStack(alignment: .leading, spacing: 8) {
                        TextField(ui("参考记忆主题（可选）"), text: $model.replySubject)
                        TextField(ui("这次怎么回复，例如：简短，明确下一步"), text: $model.replyStyle)
                        Button(ui("设为此应用的默认回复偏好")) { model.saveStyle() }
                        if !model.reviewedResult.isEmpty {
                            Toggle(ui("参考已确认的任务结果"), isOn: $model.includeReviewedResult)
                        }
                        Button(ui("按这些选项重新起草")) { model.draftReply() }.disabled(model.working)
                        ForEach(model.replyEvidence.prefix(4)) { quote in
                            Text(quote.text).font(.caption).textSelection(.enabled)
                        }
                    }.padding(.top, 6).disabled(model.working)
                }
            }
        }
    }

    private func excerpt(_ text: String) -> some View {
        ScrollView {
            Text(text).font(.system(size: 12)).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxHeight: 100)
        .padding(9)
        .background(panel, in: .rect(cornerRadius: 8))
    }

    private func textEditor(_ text: Binding<String>, label: String, height: CGFloat) -> some View {
        TextEditor(text: text)
            .font(.system(size: 13))
            .scrollContentBackground(.hidden)
            .frame(height: height)
            .padding(6)
            .background(.black.opacity(0.30), in: .rect(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(rule).allowsHitTesting(false))
            .accessibilityLabel(ui(label))
    }

    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8, content: content)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(13)
            .background(panel, in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(rule).allowsHitTesting(false))
    }

    private var settings: some View {
        VStack(alignment: .leading, spacing: 15) {
            heading("设置", "选择工作区、配置提醒，或查看快捷键。")
            Picker(ui("显示语言"), selection: $model.displayLanguage) {
                ForEach(TwinLanguage.allCases, id: \.self) { language in
                    Text(language.title).tag(language)
                }
            }.pickerStyle(.segmented)
                .help(ui("由此设置记忆图的默认语言。"))
            settingsCategories
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    switch model.settingsSection {
                    case .reminders: reminderSettings
                    case .workspace: workspaceSettings
                    case .capture: recordingSettings
                    case .shortcuts: shortcutSettings
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(height: 272)
            .scrollBounceBehavior(.basedOnSize)
            HStack {
                Spacer()
                Button(ui("保存设置")) { model.settings = false; model.configure() }
                    .buttonStyle(.borderedProminent).tint(lavender).keyboardShortcut(.defaultAction)
            }.disabled(model.working)
        }
        .padding(19).frame(width: Self.panelWidth)
        .background(ink).preferredColorScheme(.dark)
    }

    private var settingsCategories: some View {
        let columns = Array(repeating: GridItem(.flexible(minimum: 0), spacing: 6), count: 2)
        return LazyVGrid(columns: columns, spacing: 6) {
            ForEach(TwinSettingsSection.allCases, id: \.self) { section in
                let selected = model.settingsSection == section
                Button {
                    model.settingsSection = section
                } label: {
                    Text(ui(section.rawValue))
                        .font(.system(size: 12, weight: selected ? .semibold : .medium))
                        .foregroundStyle(selected ? lavender : secondaryText)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 10)
                        .frame(maxWidth: .infinity, minHeight: 30, alignment: .leading)
                        .background(selected ? lavender.opacity(0.14) : panel, in: .rect(cornerRadius: 7))
                        .overlay {
                            RoundedRectangle(cornerRadius: 7)
                                .strokeBorder(selected ? lavender.opacity(0.38) : rule)
                                .allowsHitTesting(false)
                        }
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(ui("设置分类"))
    }

    private var reminderSettings: some View {
        VStack(alignment: .leading, spacing: 12) {
            card {
                Text(ui("新记忆提醒")).font(.headline)
                Text(ui("新增或更新的记忆提到这个主题时，小人会提醒你。")).font(.caption).foregroundStyle(secondaryText)
                TextField(ui("关注主题"), text: $model.subject)
                TextField(ui("有进展后要做的事（可选）"), text: $model.watchTask, axis: .vertical).lineLimit(1...3)
            }
            card {
                Toggle(ui("在回复编辑区显示小人提示"),
                       isOn: Binding(get: { model.detectReplyEditors }, set: model.setReplyEditorHints))
                Text(ui("适用于 Slack、飞书桌面端和 Gmail 网页。停在编辑区几秒后提示。"))
                    .font(.caption).foregroundStyle(secondaryText)
                TwinActionRow {
                    Label(ui(model.editorPermissionGranted ? "辅助功能已授权" : "尚未获得辅助功能授权"),
                          systemImage: model.editorPermissionGranted ? "checkmark.circle" : "lock.circle")
                        .font(.system(size: 12)).foregroundStyle(secondaryText)
                    if !model.editorPermissionGranted {
                        Button(ui("打开系统设置")) { model.requestEditorPermission() }
                    }
                }
            }
        }
    }

    private var shortcutSettings: some View {
        card {
            shortcutRow("在转盘或记忆页导入文件", "⌘O")
            shortcutRow("在转盘或记忆页截屏", "⇧⌘S")
            shortcutRow("在转盘或记忆页开始或停止录音", "⇧⌘R")
            shortcutRow("面板内切换总览、记忆、任务、回复", "⌘1–4")
            if model.shortcutAvailable { shortcutRow("选中原消息后全局起草", "⌃⌥R") }
            shortcutRow("收起面板", "Esc")
        }
    }

    private func shortcutRow(_ label: String, _ shortcut: String) -> some View {
        HStack {
            Text(ui(label)).foregroundStyle(secondaryText).fixedSize(horizontal: false, vertical: true)
            Spacer()
            Text(shortcut).fontDesign(.monospaced).fixedSize()
        }.font(.system(size: 11))
    }

    private var workspaceSettings: some View {
        card {
            pathField("KITE 记忆工作区", $model.workspace, \.workspace, directory: true)
            pathField("执行任务的项目", $model.project, \.project, directory: true)
            Picker(ui("任务交给"), selection: $model.host) {
                Text("Codex").tag("codex"); Text("Claude Code").tag("claude")
            }
            DisclosureGroup(ui("KITE 命令路径")) {
                pathField("本机 kite-tool", $model.executable, \.executable, directory: false)
            }
        }
    }

    private var recordingSettings: some View {
        VStack(alignment: .leading, spacing: 12) {
            card {
                Text(ui("截屏权限")).font(.headline)
                Text(ui(model.screenCaptureGranted ? "当前运行的 KITE Twin 已获准截屏。" : "当前运行的 KITE Twin 尚未取得截屏权限。"))
                    .font(.caption).foregroundStyle(secondaryText)
                TwinActionRow {
                    Button(ui("打开录屏权限设置")) { model.openScreenPermissionSettings() }
                    Button(ui("重新检测")) { model.checkScreenPermission() }
                }
            }
            card {
                Text(ui("录音转写")).font(.headline)
                Text(ui("选一个本机 Whisper 模型，用于将录音转成可搜索的文字。"))
                    .font(.caption).foregroundStyle(secondaryText)
                pathField("Whisper 模型文件", $model.whisperModel, \.whisperModel, directory: false)
                DisclosureGroup(ui("转写命令路径")) {
                    pathField("whisper-cli（可选）", $model.whisper, \.whisper, directory: false)
                }
            }
            card {
                Picker(ui("小人颜色"), selection: $model.avatarShade) {
                    ForEach(TwinShade.allCases, id: \.self) { shade in
                        Text(ui(shade.title)).tag(shade)
                    }
                }
                Toggle(ui("让画像改变小人外观"), isOn: $model.useProfileAppearance)
            }
            card {
                Text(ui("转写中的说话人")).font(.headline)
                Text(ui("填入转写中代表你的名字，KITE 就能从你的发言里提炼画像。"))
                    .font(.caption).foregroundStyle(secondaryText)
                TextField(ui("我的说话人名字（可用逗号分隔）"), text: $model.selfSpeakers)
            }
        }
    }

    private func pathField(_ label: String, _ text: Binding<String>, _ field: ReferenceWritableKeyPath<TwinModel, String>, directory: Bool) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(ui(label)).font(.system(size: 12, weight: .medium))
            HStack {
                TextField(ui(label), text: text).labelsHidden()
                Button(ui("选择")) { model.pick(field, directory: directory) }
            }
        }
    }

    private var teach: some View {
        VStack(alignment: .leading, spacing: 12) {
            heading("保存为项目技能", "核对名称、使用场景和做法，再保存到当前项目。")
            TextField(ui("技能英文名，例如 release-followup"), text: $model.skillName)
            TextField(ui("何时使用"), text: $model.skillDescription)
            textEditor($model.skillSteps, label: "技能做法", height: 150)
            feedback
            HStack {
                Button(ui("取消"), role: .cancel) { model.teach = false }
                Spacer()
                Button(ui("保存技能")) { model.saveSkill() }.buttonStyle(.borderedProminent).tint(lavender)
                    .disabled(model.working || model.skillName.isEmpty || model.skillDescription.isEmpty || model.skillSteps.isEmpty)
            }
        }.padding(19).frame(width: 440).background(ink).preferredColorScheme(.dark)
    }
}

enum TwinHint {
    case memory(Quote)
    case reply(ReplyChannel, NSRunningApplication)
}

struct TwinBubble: View {
    let hint: TwinHint
    let subject: String
    let shortcutAvailable: Bool
    let language: TwinLanguage
    let onAccept: () -> Void
    let onDismiss: () -> Void
    private func ui(_ text: String) -> String { language.text(text) }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            switch hint {
            case .memory(let quote):
                Label(subject, systemImage: "clock.arrow.circlepath").font(.headline)
                Text(quote.text).font(.system(size: 12)).foregroundStyle(secondaryText).lineLimit(3)
                actions("查看任务")
            case .reply(let channel, _):
                Label(language == .english ? "Draft a reply in \(channel.rawValue)" : "在 \(channel.rawValue) 起草回复",
                      systemImage: "arrowshape.turn.up.left").font(.headline)
                Text(ui(shortcutAvailable ? "选中要回复的原消息，再按 ⌃⌥R。" : "选中原消息，再从「服务」菜单起草。"))
                    .font(.system(size: 12)).foregroundStyle(secondaryText)
                actions("打开回复")
            }
        }
        .padding(15)
        .frame(width: 324, alignment: .leading)
        .foregroundStyle(.white)
        .background(ink, in: .rect(cornerRadius: 13))
        .overlay(RoundedRectangle(cornerRadius: 13).strokeBorder(rule).allowsHitTesting(false))
        .preferredColorScheme(.dark)
        .fixedSize(horizontal: true, vertical: true)
    }

    private func actions(_ label: String) -> some View {
        HStack(spacing: 10) {
            Button(ui(label), action: onAccept).buttonStyle(.borderedProminent).tint(lavender)
            Button(ui("稍后"), action: onDismiss).buttonStyle(.borderless).foregroundStyle(secondaryText)
        }
    }
}
