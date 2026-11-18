import Foundation

@main
struct MemoketSpeakerTest {
    static func main() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        func recording(_ participants: String) throws -> MemoketRecording {
            try decoder.decode(MemoketRecording.self, from: Data("""
                {"id":"recording","title":"Test conversation","participant_details":\(participants)}
                """.utf8))
        }

        let marked = try recording("""
            [{"participant_id":"1","name":"A","is_self":true},
             {"participant_id":"2","name":"B"}]
            """)
        precondition(marked.accountSpeaker == "A")

        let unmarked = try recording("""
            [{"participant_id":"1","name":"A","role":"Chief Technology Officer"},
             {"participant_id":"2","name":"B","is_self":false}]
            """)
        precondition(unmarked.accountSpeaker == nil)

        let ambiguous = try recording("""
            [{"participant_id":"1","name":"Alex","is_self":true},
             {"participant_id":"2","name":"ALEx"}]
            """)
        precondition(ambiguous.accountSpeaker == nil)

        let multiple = try recording("""
            [{"participant_id":"1","name":"Joe","is_self":true},
             {"participant_id":"2","name":"Alex","is_self":true}]
            """)
        precondition(multiple.accountSpeaker == nil)
    }
}
