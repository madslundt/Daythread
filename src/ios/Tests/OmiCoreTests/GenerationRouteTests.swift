import XCTest
@testable import OmiCore

final class GenerationRouteTests: XCTestCase {
    func testOnlyGenerationPostsBecomeDurableJobs() {
        XCTAssertEqual(GenerationRoute(path: "v1/rewind", method: "POST")?.kind, "rewind")
        XCTAssertEqual(GenerationRoute(path: "v1/recaps/2026-10-17", method: "POST")?.parameters, ["day": "2026-10-17"])
        XCTAssertEqual(GenerationRoute(path: "v1/transcripts/source-1/translation", method: "POST")?.parameters, ["source_id": "source-1"])
        XCTAssertEqual(GenerationRoute(path: "v1/meetings/meeting-1/recap", method: "POST")?.kind, "meeting_recap")
        for path in ["v1/rewind/audio", "v1/recaps/2026-10-17/feedback", "v1/transcripts/source-1/corrections", "v1/jobs", "v1/weekly-reviews/2026-10-12"] {
            XCTAssertNil(GenerationRoute(path: path, method: "POST"))
        }
        XCTAssertNil(GenerationRoute(path: "v1/transcripts/source-1/translation", method: "GET"))
        XCTAssertNil(GenerationRoute(path: "v1/recaps/2026-10-17", method: "DELETE"))
    }
}
