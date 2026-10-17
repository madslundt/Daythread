import XCTest
@testable import OmiCore

final class AppLocalizationTests: XCTestCase {
    func testInterpolatedMessagesHaveStableKeysAndPreserveValues() {
        let message: AppLocalization.Message = "Imported \(2) recordings from \("Today")"
        XCTAssertEqual(message.key, "Imported {value0} recordings from {value1}")
        XCTAssertEqual(message.source, "Imported 2 recordings from Today")
        let catalog = [message.key: "Fra {value1}: {value0} optagelser", "Today": "I dag"]
        XCTAssertEqual(AppLocalization.resolve(message.key, values: message.values, translations: catalog),
                       "Fra Today: 2 optagelser")
    }

    func testStoredAndServerMessagesUseTheSameTemplates() {
        let catalog = ["Upload part {value0} failed": "Upload af del {value0} mislykkedes"]
        XCTAssertEqual(AppLocalization.resolve("Upload part 3 failed", translations: catalog),
                       "Upload af del 3 mislykkedes")
        XCTAssertEqual(AppLocalization.resolve("Provider diagnostic", translations: catalog), "Provider diagnostic")
    }

    func testParameterTextIsNeverTreatedAsAnotherPlaceholder() {
        XCTAssertEqual(AppLocalization.resolve("Name: {value0}; count: {value1}",
            values: ["value0": "{value1}", "value1": "2"],
            translations: ["Name: {value0}; count: {value1}": "Navn: {value0}; antal: {value1}"]),
            "Navn: {value1}; antal: 2")
    }
}
