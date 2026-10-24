import UIKit
import OmiCore

extension ShareViewController {
    func configureOptionButton(_ button: UIButton, title: String) {
        var style = UIButton.Configuration.gray()
        style.cornerStyle = .large
        style.contentInsets = NSDirectionalEdgeInsets(top: 11, leading: 12, bottom: 11, trailing: 12)
        button.configuration = style
        button.setTitle(title, for: .normal)
    }

    func refreshOptionMenus() {
        let tagCount = selectedTagIDs.count + pendingTagNames.count
        let tagTitle = tagCount == 0 ? "Tags (optional)" : "Tags · \(tagCount)"
        tagsButton.setTitle(tagTitle, for: .normal)
        var tagActions = availableTags.map { tag in
            UIAction(title: tag.name, state: selectedTagIDs.contains(tag.id) ? .on : .off) { [weak self] _ in
                guard let self, !self.busy else { return }
                if self.selectedTagIDs.contains(tag.id) { self.selectedTagIDs.remove(tag.id) }
                else { self.selectedTagIDs.insert(tag.id) }
                self.refreshOptionMenus()
            }
        }
        tagActions += pendingTagNames.sorted().map { name in
            UIAction(title: name, state: .on) { [weak self] _ in
                guard let self, !self.busy else { return }
                self.pendingTagNames.remove(name)
                self.refreshOptionMenus()
            }
        }
        tagActions.append(UIAction(title: AppLocalization.text("Create tag…"), image: UIImage(systemName: "plus")) { [weak self] _ in
            self?.createTag()
        })
        tagsButton.menu = UIMenu(title: "Add tags", children: tagActions)

        if let currentLocation { locationButton.setTitle(String(format: "Location · %.3f, %.3f", currentLocation.latitude, currentLocation.longitude), for: .normal) }
        else if let selectedPlace,
                let zone = locationZones.first(where: { $0.id == selectedPlace.zoneID }),
                let place = zone.locations.first(where: { $0.id == selectedPlace.locationID }) {
            locationButton.setTitle("\(zone.name) · \(place.label)", for: .normal)
        } else { locationButton.setTitle("Location (optional)", for: .normal) }
        var locationActions = [UIAction(title: "No location", state: selectedPlace == nil && currentLocation == nil ? .on : .off) { [weak self] _ in
            self?.selectedPlace = nil; self?.currentLocation = nil; self?.refreshOptionMenus()
        }]
        locationActions.append(UIAction(title: "Use current location", image: UIImage(systemName: "location")) { [weak self] _ in
            guard let self else { return }
            Task {
                do {
                    let fix = try await self.locationReader.currentLocation()
                    self.currentLocation = CaptureLocation(latitude: fix.coordinate.latitude, longitude: fix.coordinate.longitude,
                                                           horizontalAccuracy: max(0, fix.horizontalAccuracy), capturedAt: fix.timestamp)
                    self.selectedPlace = nil
                    self.refreshOptionMenus()
                } catch { self.status.text = error.localizedDescription }
            }
        })
        for zone in locationZones {
            for place in zone.locations {
                let selected = selectedPlace?.zoneID == zone.id && selectedPlace?.locationID == place.id
                locationActions.append(UIAction(title: "\(zone.name) · \(place.label)", state: selected ? .on : .off) { [weak self] _ in
                    self?.selectedPlace = (zone.id, place.id); self?.currentLocation = nil; self?.refreshOptionMenus()
                })
            }
        }
        locationButton.menu = UIMenu(title: "Add a location", children: locationActions)
    }

    func loadOptions() {
        guard let endpoints else { return }
        Task {
            do {
                let tagData = try await send("v1/tags", method: "GET", configuration: endpoints)
                availableTags = try JSONDecoder().decode(ShareTagDirectory.self, from: tagData).items
            } catch { }
            do {
                let placeData = try await send("v1/location-zones", method: "GET", configuration: endpoints)
                locationZones = try JSONDecoder().decode(ShareZoneDirectory.self, from: placeData).items
            } catch { }
            refreshOptionMenus()
        }
    }

}

extension ShareViewController {
    func updateImportAvailability() {
        saveButton.isEnabled = !busy && !staging && endpoints != nil && !profileID.isEmpty
            && !stagedURLs.isEmpty && stagedURLs.allSatisfy { timeEditors[$0]?.isConfirmed == true }
    }

    func setOptionsEnabled(_ enabled: Bool) {
        tagsButton.isEnabled = enabled
        locationButton.isEnabled = enabled
        for editor in timeEditors.values { editor.setEnabled(enabled) }
    }

    func createTag() {
        guard !busy else { return }
        let alert = UIAlertController(title: AppLocalization.text("New tag name"),
                                      message: AppLocalization.text("New tags are saved with imported recordings."), preferredStyle: .alert)
        alert.addTextField { field in
            field.placeholder = AppLocalization.text("New tag name")
            field.autocapitalizationType = .sentences
            field.accessibilityIdentifier = "share.newTagName"
        }
        alert.addAction(UIAlertAction(title: AppLocalization.text("Cancel"), style: .cancel))
        alert.addAction(UIAlertAction(title: AppLocalization.text("Add tag"), style: .default) { [weak self, weak alert] _ in
            guard let self, !self.busy else { return }
            let name = (alert?.textFields?.first?.text ?? "").precomposedStringWithCanonicalMapping.split(whereSeparator: \.isWhitespace).joined(separator: " ")
            guard !name.isEmpty, name.unicodeScalars.count <= 80,
                  !name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
                  self.selectedTagIDs.count + self.pendingTagNames.count < 50 else {
                self.status.text = AppLocalization.text("Enter a tag name of 1 to 80 characters.")
                return
            }
            if let tag = self.availableTags.first(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame }) {
                self.selectedTagIDs.insert(tag.id)
            } else if !self.pendingTagNames.contains(where: { $0.caseInsensitiveCompare(name) == .orderedSame }) {
                self.pendingTagNames.insert(name)
            }
            self.refreshOptionMenus()
        })
        present(alert, animated: true)
    }
}
