import Foundation

struct MemoketRecording: Identifiable, Decodable, Sendable {
    struct Participant: Decodable, Sendable {
        let participantId: String?
        let name: String?
        let isSelf: Bool?
    }

    let id: String
    let title: String
    let startedAt: String?
    let participantDetails: [Participant]?

    var accountSpeaker: String? {
        let participants = participantDetails ?? []
        let matches = participants.filter { $0.isSelf == true }
        guard matches.count == 1, let owner = matches.first,
              let id = owner.participantId, !id.isEmpty,
              let name = owner.name?.trimmingCharacters(in: .whitespacesAndNewlines), !name.isEmpty,
              participants.filter({ $0.participantId == id }).count == 1,
              participants.filter({ Self.speakerKey($0.name ?? "") == Self.speakerKey(name) }).count == 1 else {
            return nil
        }
        return name
    }

    static func speakerKey(_ name: String) -> String {
        name.trimmingCharacters(in: .whitespacesAndNewlines)
            .precomposedStringWithCompatibilityMapping
            .folding(options: .caseInsensitive, locale: Locale(identifier: "en_US_POSIX"))
    }

    var date: Date? {
        guard let startedAt else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions.insert(.withFractionalSeconds)
        if let date = formatter.date(from: startedAt) { return date }
        formatter.formatOptions.remove(.withFractionalSeconds)
        return formatter.date(from: startedAt)
    }
}

struct MemoketRecordingsPage: Decodable, Sendable {
    let items: [MemoketRecording]
    let nextOffset: Int?
}

private struct MemoketTranscriptResponse: Decodable {
    struct Page: Decodable {
        struct Line: Decodable {
            let id: String
            let text: String
            let speaker: String?
            let startedOffsetMs: Int?
            let endedOffsetMs: Int?
            let textOffset: Int?
        }

        let conversationId: String
        let lines: [Line]
        let hasMore: Bool
        let nextOffset: Int?
        let nextTextOffset: Int?
    }

    let items: [Page]
}

enum MemoketCLI {
    static let docs = URL(string: "https://memoket.ai/pages/connector")!

    private static var installDirectory: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/Application Support/KITE Twin/Memoket CLI", directoryHint: .isDirectory)
    }

    static var executable: String? {
        let override = ProcessInfo.processInfo.environment["KITE_TWIN_MEMOKET_CLI"]
        return [override, installDirectory.appending(path: "bin/memoket").path, executableOnPath("memoket")]
            .compactMap { $0 }
            .first { $0.hasPrefix("/") && FileManager.default.isExecutableFile(atPath: $0) }
    }

    static func install() async throws {
        guard let npm = executableOnPath("npm") else {
            throw TwinError("没有找到 npm。请先安装 Node.js 18 或更新版本。")
        }
        try await Task.detached(priority: .userInitiated) {
            let result = try LocalProcess.run(npm, [
                "install", "--global", "--prefix", installDirectory.path, "--ignore-scripts",
                "--no-audit", "--no-fund", "--registry=https://registry.npmjs.org/", "@memoket-ai/cli@3.2.1",
            ])
            guard result.status == 0, executable != nil else {
                throw TwinError("安装 Memoket CLI 失败。请检查网络后重试，或按官方说明安装。")
            }
        }.value
    }

    static func signIn() async throws {
        guard let executable else { throw TwinError("请先安装 Memoket CLI。") }
        try await Task.detached(priority: .userInitiated) {
            let result = try LocalProcess.run(executable, ["login"])
            guard result.status == 0 else {
                throw TwinError("未完成 Memoket 登录。请检查浏览器后重试。")
            }
        }.value
    }

    static func list(offset: Int = 0) async throws -> MemoketRecordingsPage {
        guard let executable else { throw TwinError("请先安装 Memoket CLI。") }
        return try await Task.detached(priority: .userInitiated) {
            try call(executable, "list_conversations", ["count": 15, "offset": offset])
        }.value
    }

    static func prepare(_ recording: MemoketRecording, refreshParticipants: Bool = true) async throws -> Capture {
        guard let executable else { throw TwinError("请先安装 Memoket CLI。") }
        return try await Task.detached(priority: .userInitiated) {
            let turns = try transcript(executable, recording.id)
            let details: MemoketRecordingsPage? = refreshParticipants
                ? try? call(executable, "get_conversations", ["conversation_ids": [recording.id]])
                : nil
            let fresh = details?.items.first { $0.id == recording.id }
            let selfSpeaker = (fresh ?? recording).accountSpeaker.flatMap { candidate in
                turns.first { MemoketRecording.speakerKey($0.speaker ?? "") == MemoketRecording.speakerKey(candidate) }?.speaker
            }
            let folder = try LocalProcess.scratch()
            do {
                let original = folder.appending(path: "Memoket Transcript.txt")
                let text = turns.map { turn in
                    let time = turn.startMs.map { "[\($0) ms] " } ?? ""
                    let speaker = turn.speaker.map { "\($0): " } ?? ""
                    return time + speaker + turn.text
                }.joined(separator: "\n")
                try text.write(to: original, atomically: true, encoding: .utf8)
                let title = recording.title.trimmingCharacters(in: .whitespacesAndNewlines)
                return Capture(kind: "memoket", url: original, title: title.isEmpty ? "未命名录音" : String(title.prefix(300)),
                               date: recording.date ?? .now, scratch: folder, transcript: turns,
                               memoketID: recording.id, memoketStartedAt: recording.date, memoketSelfSpeaker: selfSpeaker)
            } catch {
                try? FileManager.default.removeItem(at: folder)
                throw error
            }
        }.value
    }

    private static func transcript(_ executable: String, _ id: String) throws -> [SpokenTurn] {
        var offset = 0
        var textOffset = 0
        var turns: [SpokenTurn] = []
        while true {
            let response: MemoketTranscriptResponse = try call(executable, "get_transcripts", [
                "conversation_id": id, "offset": offset, "text_offset": textOffset,
            ])
            guard let page = response.items.first, page.conversationId == id else {
                throw TwinError("找不到这条录音的转写。")
            }
            for line in page.lines {
                if (line.textOffset ?? 0) > 0 {
                    guard var previous = turns.popLast(), previous.segmentId == line.id else {
                        throw TwinError("转写分页不完整。请重新打开这条录音。")
                    }
                    previous.text += line.text
                    turns.append(previous)
                } else {
                    turns.append(SpokenTurn(segmentId: line.id, text: line.text,
                                            startMs: line.startedOffsetMs, endMs: line.endedOffsetMs,
                                            speaker: line.speaker))
                }
            }
            if !page.hasMore { break }
            let nextTextOffset = page.nextTextOffset ?? 0
            guard let nextOffset = page.nextOffset,
                  (nextOffset, nextTextOffset) > (offset, textOffset) else {
                throw TwinError("没有收到下一页转写。请重试。")
            }
            offset = nextOffset
            textOffset = nextTextOffset
        }
        let nonempty = turns.filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        guard !nonempty.isEmpty else { throw TwinError("这条录音还没有可导入的转写。") }
        return nonempty
    }

    private static func call<Response: Decodable>(_ executable: String, _ operation: String,
                                                   _ arguments: [String: Any]) throws -> Response {
        let body = try JSONSerialization.data(withJSONObject: arguments)
        let result = try LocalProcess.run(executable, ["call", operation, "--json", String(decoding: body, as: UTF8.self)])
        if result.status != 0 {
            let diagnostics = result.stderr.lowercased()
            if diagnostics.contains("not logged in") || diagnostics.contains("invalid_token")
                || diagnostics.contains("unauthorized") || diagnostics.contains("(401)")
                || diagnostics.contains("session expired") {
                throw TwinError("请先登录 Memoket，再读取录音。")
            }
            throw TwinError("Memoket 暂时无法读取录音。请检查网络和账号权限后重试。")
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do {
            return try decoder.decode(Response.self, from: result.stdout)
        } catch {
            throw TwinError("Memoket 返回了无法读取的结果。请检查 CLI 版本后重试。")
        }
    }

    private static func executableOnPath(_ name: String) -> String? {
        LocalProcess.environment["PATH"]?.split(separator: ":").lazy
            .map { String($0) + "/" + name }
            .first { $0.hasPrefix("/") && FileManager.default.isExecutableFile(atPath: $0) }
    }
}
