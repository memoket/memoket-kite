import Foundation

struct BrowserReply: Decodable {
    let id: String
    let channel: ReplyChannel
    let text: String

    struct Result: Decodable {
        let attempt: String
        let ok: Bool
        let error: String
    }

    static let directory = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/KITE Twin/Browser Replies", isDirectory: true)
    private var folder: URL { Self.directory.appendingPathComponent(id, isDirectory: true) }
    var isConnected: Bool { FileManager.default.fileExists(atPath: folder.path) }

    static func pending() -> BrowserReply? {
        let files = (try? FileManager.default.contentsOfDirectory(at: directory,
                      includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey])) ?? []
        for file in files.prefix(30) {
            let name = file.lastPathComponent
            guard UUID(uuidString: name) != nil, name == name.lowercased(),
                  let info = try? file.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey]),
                  info.isDirectory == true, info.isSymbolicLink == false,
                  !FileManager.default.fileExists(atPath: file.appendingPathComponent("accepted").path)
            else { continue }
            let source = file.appendingPathComponent("begin.json")
            guard let properties = try? source.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey, .contentModificationDateKey]),
                  properties.isRegularFile == true, let size = properties.fileSize, size <= 65536,
                  let date = properties.contentModificationDate, abs(date.timeIntervalSinceNow) <= 15,
                  let data = try? Data(contentsOf: source),
                  let request = try? JSONDecoder().decode(Self.self, from: data),
                  request.id == name, request.channel != .x, !request.text.isEmpty, request.text.count <= 6000,
                  (try? Data().write(to: file.appendingPathComponent("accepted"), options: [.atomic])) != nil
            else { continue }
            return request
        }
        return nil
    }

    func close() {
        try? Data().write(to: folder.appendingPathComponent("closed"), options: [.atomic])
    }

    func fill(_ draft: String) async throws {
        guard isConnected else { throw TwinError("网页回复已断开。请在原网页重新起草。") }
        let attempt = UUID().uuidString
        try JSONSerialization.data(withJSONObject: ["draft": draft, "attempt": attempt])
            .write(to: folder.appendingPathComponent("fill.json"), options: [.atomic])
        let resultURL = folder.appendingPathComponent("result.json")
        for _ in 0..<72 {
            try await Task.sleep(for: .milliseconds(120))
            guard isConnected else { throw TwinError("浏览器回复桥已断开，草稿保留在 KITE。") }
            if let data = try? Data(contentsOf: resultURL),
               let result = try? JSONDecoder().decode(Result.self, from: data), result.attempt == attempt {
                try? FileManager.default.removeItem(at: resultURL)
                guard result.ok else { throw TwinError(result.error) }
                close()
                return
            }
        }
        // Don't queue another write while a late response could still arrive.
        close()
        throw TwinError("未收到网页回填确认。请检查原回复框，草稿仍保留在 KITE。")
    }
}
