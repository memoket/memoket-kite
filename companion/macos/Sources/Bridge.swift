import AppKit
import Foundation
import ImageIO
import PDFKit
import UniformTypeIdentifiers
import Vision

struct Original: Identifiable, Decodable {
    let id: String
    let title: String
    let kind: String
    let original: String?
}

struct Quote: Identifiable, Decodable {
    let id: String
    let citation: String
    let text: String
    let date: String?
    let speaker: String?
    let startMs: Int?
}

struct SavedSkill: Identifiable, Decodable {
    var id: String { path }
    let name: String
    let description: String?
    let instructions: String
    let path: String
}

struct SpokenTurn: Decodable {
    let segmentId: String
    var text: String
    let startMs: Int?
    let endMs: Int?
    var speaker: String? = nil

    var payload: [String: Any] {
        var value: [String: Any] = ["segment_id": segmentId, "text": text]
        if let startMs { value["start_ms"] = startMs }
        if let endMs { value["end_ms"] = endMs }
        if let speaker { value["speaker_id"] = speaker }
        return value
    }
}

struct ProfileEvidence: Decodable, Identifiable {
    var id: String { field }
    let field: String
    let quote: String
    let summary: String
    let edited: Bool?
    let citation: String
    let title: String

    var label: String {
        switch field {
        case "background": "背景"
        case "preferences": "偏好"
        case "work_habits": "工作习惯"
        case "communication": "沟通偏好"
        case "favorite_color": "喜欢的颜色"
        default: field
        }
    }
}

struct Reply: Decodable {
    struct Failure: Decodable { let message: String }
    let ok: Bool
    var error: Failure?
    var version: Int?
    var profile: [ProfileEvidence]?
    var profileUsed: [String]?
    var subjectMissing: Bool?
    var runId: String?
    var workspaceId: String?
    var sourceCount: Int?
    var items: [Original]?
    var created: Bool?
    var reminders: [Quote]?
    var evidence: [Quote]?
    var result: String?
    var path: String?
    var skills: [SavedSkill]?
    var transcript: [SpokenTurn]?
    var sourceDigest: String?
    var skillName: String?
    var skillDescription: String?
    var skillSteps: String?
    var retained: [String]?
    var prompt: String?
    var projectPath: String?
}

struct TwinError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct CommandResult {
    let status: Int32
    let stdout: Data
    let stderr: String
}

enum LocalProcess {
    static var environment: [String: String] {
        var environment = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let paths = ["/opt/homebrew/bin", "/usr/local/bin", "\(home)/.local/bin", "/usr/bin", "/bin", "/usr/sbin"]
        environment["PATH"] = ([environment["PATH"] ?? ""] + paths).joined(separator: ":")
        return environment
    }

    static func scratch() throws -> URL {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("kite-twin-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
        return folder
    }

    static func run(_ executable: String, _ arguments: [String], input: Data = Data()) throws -> CommandResult {
        let folder = try scratch()
        defer { try? FileManager.default.removeItem(at: folder) }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.environment = environment
        let contents = [input, Data(), Data()]
        let paths = try contents.enumerated().map { index, data in
            let path = folder.appendingPathComponent("\(index)")
            try data.write(to: path)
            return path
        }
        let stdin = try FileHandle(forReadingFrom: paths[0])
        let stdout = try FileHandle(forWritingTo: paths[1])
        let stderr = try FileHandle(forWritingTo: paths[2])
        defer { try? stdin.close(); try? stdout.close(); try? stderr.close() }
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr
        try process.run()
        process.waitUntilExit()
        return try CommandResult(status: process.terminationStatus, stdout: Data(contentsOf: paths[1]),
                                 stderr: String(contentsOf: paths[2], encoding: .utf8))
    }
}

struct Bridge {
    let executable: String
    let workspace: String

    func call(_ action: String, _ value: [String: Any] = [:]) async throws -> Reply {
        let input = try JSONSerialization.data(withJSONObject: value)
        return try await Task.detached(priority: .userInitiated) {
            let result = try LocalProcess.run(executable, ["--workspace", workspace, "twin", action], input: input)
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            guard let reply = try? decoder.decode(Reply.self, from: result.stdout) else {
                throw TwinError(result.stderr.isEmpty ? "KITE CLI 需要支持 twin 命令。" : result.stderr)
            }
            guard result.status == 0 && reply.ok else { throw TwinError(reply.error?.message ?? "KITE 执行失败。") }
            return reply
        }.value
    }

    func graph(language: TwinLanguage) async throws {
        try await Task.detached {
            let result = try LocalProcess.run(executable, ["--workspace", workspace, "view", "--lang", language.rawValue])
            guard result.status == 0 else {
                throw TwinError(String(decoding: result.stdout, as: UTF8.self))
            }
        }.value
    }
}

struct Capture {
    let kind: String
    let url: URL
    let title: String
    let date: Date
    var segments: [[String: String]] = []
    var scratch: URL?
    var transcript: [SpokenTurn]?
    var digest: String?
    var memoketID: String?
    var memoketStartedAt: Date?
    var memoketSelfSpeaker: String?

    func payload(note: String, model: String, whisper: String) -> [String: Any] {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = .current
        var result: [String: Any] = ["kind": kind, "title": title, "file": url.path,
                                     "started_at": formatter.string(from: date)]
        if kind == "recording" {
            result["model"] = model
            if !whisper.isEmpty { result["whisper"] = whisper }
            if let digest, let transcript {
                result["prepared_sha256"] = digest
                result["segments"] = transcript.map(\.payload)
            }
        } else if let memoketID, let transcript {
            result["memoket_id"] = memoketID
            result["started_at"] = memoketStartedAt.map { formatter.string(from: $0) }
            result["segments"] = transcript.map(\.payload)
            result["memoket_self_speaker"] = memoketSelfSpeaker
        } else {
            result["segments"] = segments
        }
        let trimmed = note.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty { result["note"] = trimmed }
        return result
    }

    static func screenshot(_ image: CGImage) async throws -> Capture {
        try await Task.detached(priority: .userInitiated) {
            let folder = try LocalProcess.scratch()
            let path = folder.appendingPathComponent("Screenshot.png")
            do {
                guard let output = CGImageDestinationCreateWithURL(path as CFURL, UTType.png.identifier as CFString, 1, nil) else {
                    throw TwinError("无法创建截屏文件。")
                }
                CGImageDestinationAddImage(output, image, nil)
                guard CGImageDestinationFinalize(output) else { throw TwinError("无法写入截屏文件。") }
                let request = VNRecognizeTextRequest()
                request.recognitionLevel = .accurate
                request.automaticallyDetectsLanguage = true
                try VNImageRequestHandler(url: path).perform([request])
                let rows = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
                return Capture(kind: "screenshot", url: path, title: "屏幕截图", date: Date(),
                               segments: lines(rows, prefix: "image-line"), scratch: folder)
            } catch {
                try? FileManager.default.removeItem(at: folder)
                throw error
            }
        }.value
    }

    static func document(_ path: URL) async throws -> Capture {
        try await Task.detached(priority: .userInitiated) {
            let extensionName = path.pathExtension.lowercased()
            let rows: [[String: String]]
            switch extensionName {
            case "pdf":
                guard let pdf = PDFDocument(url: path), !pdf.isLocked else { throw TwinError("无法读取这份 PDF。") }
                rows = (0..<pdf.pageCount).flatMap { index in
                    lines(pdf.page(at: index)?.string?.components(separatedBy: .newlines) ?? [],
                          prefix: "page-\(index + 1)-line")
                }
            case "docx", "rtf", "odt":
                let result = try LocalProcess.run("/usr/bin/textutil", ["-convert", "txt", "-stdout", path.path])
                guard result.status == 0 else { throw TwinError(result.stderr) }
                rows = lines(String(decoding: result.stdout, as: UTF8.self).components(separatedBy: .newlines), prefix: "line")
            case "txt", "md", "csv", "log":
                rows = try lines(String(contentsOf: path, encoding: .utf8).components(separatedBy: .newlines), prefix: "line")
            default:
                throw TwinError("请选择 PDF、DOCX、RTF、ODT 或文本文件。")
            }
            guard rows.count <= 10000 else { throw TwinError("单份文档最多 10,000 个非空原文行。") }
            return Capture(kind: "document", url: path, title: path.lastPathComponent, date: Date(), segments: rows)
        }.value
    }

    private static func lines(_ rows: [String], prefix: String) -> [[String: String]] {
        rows.enumerated().compactMap { i, text in
            text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil :
                ["segment_id": "\(prefix)-\(i + 1)", "text": text]
        }
    }
}
