import UIKit
import OmiCore

/// One editable timestamp per original file; frozen after its upload plan is accepted.
final class ShareRecordingTimeView: UIStackView {
    private let datePicker = UIDatePicker()
    private let referencePicker = UISegmentedControl(items: [
        AppLocalization.text("Recording ended"), AppLocalization.text("Recording started")
    ])
    private let confirmButton = UIButton(type: .system)
    private let provenance = UILabel()
    private(set) var isConfirmed: Bool
    var onChanged: (() -> Void)?
    var locked = false { didSet { setEnabled(!locked) } }
    var recordedAt: Date { datePicker.date }
    var reference: RecordingTimeReference { referencePicker.selectedSegmentIndex == 1 ? .start : .end }

    init(name: String, suggestion: RecordingDateSuggestion?) {
        isConfirmed = suggestion.map { !$0.needsConfirmation } ?? false
        super.init(frame: .zero)
        axis = .vertical
        spacing = 10
        let title = UILabel()
        title.text = name
        title.font = .preferredFont(forTextStyle: .headline)
        title.numberOfLines = 0
        addArrangedSubview(title)
        referencePicker.selectedSegmentIndex = suggestion?.reference == .start ? 1 : 0
        referencePicker.accessibilityIdentifier = "share.timeReference"
        referencePicker.addTarget(self, action: #selector(confirmTime), for: .valueChanged)
        addArrangedSubview(referencePicker)
        datePicker.datePickerMode = .dateAndTime
        datePicker.preferredDatePickerStyle = .compact
        datePicker.maximumDate = Date()
        datePicker.date = suggestion?.date ?? Date()
        datePicker.accessibilityLabel = AppLocalization.text("Recording date and time")
        datePicker.accessibilityIdentifier = "share.recordedAt"
        datePicker.addTarget(self, action: #selector(confirmTime), for: .valueChanged)
        addArrangedSubview(datePicker)
        provenance.font = .preferredFont(forTextStyle: .caption1)
        provenance.textColor = .secondaryLabel
        provenance.numberOfLines = 0
        let source = suggestion.map { AppLocalization.text("Suggested date source: \(AppLocalization.key($0.source))") }
            ?? AppLocalization.text("This file has no reliable date. Set when it was recorded.")
        provenance.text = source + "\n" + TimeZone.current.identifier
        addArrangedSubview(provenance)
        confirmButton.setTitle(AppLocalization.text("Use the time shown"), for: .normal)
        confirmButton.accessibilityIdentifier = "share.confirmTime"
        confirmButton.addTarget(self, action: #selector(confirmTime), for: .touchUpInside)
        confirmButton.isHidden = isConfirmed
        addArrangedSubview(confirmButton)
    }

    required init(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func setEnabled(_ enabled: Bool) {
        datePicker.isEnabled = enabled && !locked
        referencePicker.isEnabled = enabled && !locked
        confirmButton.isEnabled = enabled && !locked
    }

    @objc private func confirmTime() {
        isConfirmed = true
        confirmButton.isHidden = true
        onChanged?()
    }
}
