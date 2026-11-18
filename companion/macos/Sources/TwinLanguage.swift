import Foundation

enum TwinLanguage: String, CaseIterable {
    case chinese = "zh"
    case english = "en"

    var title: String { self == .chinese ? "中文" : "English" }

    private static let englishStrings: [String: String] = {
        guard let resource = Bundle.main.url(forResource: "TwinEnglish", withExtension: "json"),
              let data = try? Data(contentsOf: resource),
              let strings = try? JSONDecoder().decode([String: String].self, from: data) else {
            return [:]
        }
        return strings
    }()

    func text(_ original: String) -> String {
        self == .english ? Self.englishStrings[original] ?? original : original
    }
}
