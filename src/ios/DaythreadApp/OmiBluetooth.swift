import CoreBluetooth
import Observation
import OmiCore

@MainActor @Observable
final class OmiBluetooth: NSObject, @preconcurrency CBCentralManagerDelegate, @preconcurrency CBPeripheralDelegate {
    static let audioService = CBUUID(string: OmiDeviceDiscovery.audioService)
    static let audioID = CBUUID(string: "19b10001-e8f2-537e-4f6c-d104768a1214")
    static let codecID = CBUUID(string: "19b10002-e8f2-537e-4f6c-d104768a1214")
    static let buttonService = CBUUID(string: "23ba7924-0000-1000-7450-346eac492e92")
    static let buttonID = CBUUID(string: "23ba7925-0000-1000-7450-346eac492e92")
    static let hapticService = CBUUID(string: "CAB1AB95-2EA5-4F4D-BB56-874B72CFC984")
    static let hapticID = CBUUID(string: "CAB1AB96-2EA5-4F4D-BB56-874B72CFC984")
    var status = L10n.source("Bluetooth starting")
    var devices: [CBPeripheral] = []
    var connected = false
    var scanning = false
    var battery: Int?
    var codec: Int?
    var model = L10n.source("Unknown")
    var firmware = L10n.source("Unknown")
    var name = "Omi"
    var buttonAvailable = false
    private(set) var hapticAvailable = false
    var identifier: String { peripheral?.identifier.uuidString ?? "iphone" }
    var captureEnabled = false { didSet { if oldValue != captureEnabled { updateAudioSubscription() } } }
    @ObservationIgnored private var manager: CBCentralManager!
    @ObservationIgnored private var peripheral: CBPeripheral?
    @ObservationIgnored private var audio: CBCharacteristic?
    @ObservationIgnored private var haptic: CBCharacteristic?
    @ObservationIgnored private var pendingHapticCompletions: [@MainActor () -> Void] = []
    @ObservationIgnored private var hapticWatchdog: Task<Void, Never>?
    @ObservationIgnored private var hapticFaulted = false
    @ObservationIgnored var onAudio: ((Data, Date, TimeInterval) -> Void)?
    @ObservationIgnored var onButton: ((Data) -> Void)?
    @ObservationIgnored var onDisconnect: (() -> Void)?
    @ObservationIgnored var onCodec: ((Int) -> Void)?
    @ObservationIgnored var onError: ((String) -> Void)?
    @ObservationIgnored private var wanted = true
    @ObservationIgnored private var capabilityFailure = false
    @ObservationIgnored private var pendingSubscriptions: Set<CBUUID> = []
    @ObservationIgnored private var pendingReads: Set<CBUUID> = []
    @ObservationIgnored private var pendingCharacteristicDiscovery: Set<CBUUID> = []
    @ObservationIgnored private var discoveryRecovery = OmiDiscoveryRecovery()
    @ObservationIgnored private var discoveryWatchdog: Task<Void, Never>?
    @ObservationIgnored private var scanTimeout: Task<Void, Never>?

    override init() {
        super.init()
        manager = CBCentralManager(delegate: self, queue: .main, options: [
            CBCentralManagerOptionRestoreIdentifierKey: "com.daythread.bluetooth",
            CBCentralManagerOptionShowPowerAlertKey: true
        ])
    }
    func scan() {
        guard manager.state == .poweredOn else { status = L10n.source("Enable Bluetooth in Settings"); return }
        stopScan()
        wanted = true; scanning = true; devices = []; status = L10n.source("Looking for Omi")
        // Foreground pairing also finds firmware that advertises a name without the audio service.
        // Reconnection uses the saved peripheral identifier, including in the background.
        manager.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
        scanTimeout = Task { [weak self] in
            try? await Task.sleep(for: .seconds(30))
            guard !Task.isCancelled else { return }
            self?.stopScan()
        }
    }
    func stopScan() {
        scanTimeout?.cancel(); scanTimeout = nil
        manager.stopScan(); scanning = false
    }
    func connect(_ device: CBPeripheral) {
        wanted = true; capabilityFailure = false; stopScan()
        if let previous = peripheral, previous.identifier != device.identifier {
            manager.cancelPeripheralConnection(previous)
            lostConnection()
        }
        peripheral = device; device.delegate = self; status = L10n.source("Connecting to \(device.name ?? "Omi")")
        UserDefaults.standard.set(device.identifier.uuidString, forKey: "pairedDevice")
        manager.connect(device, options: [CBConnectPeripheralOptionNotifyOnDisconnectionKey: true])
    }
    func playHaptic(_ duration: OmiHapticDuration, completion: @escaping @MainActor () -> Void = {}) {
        guard let command = duration.command, wanted, connected,
              let peripheral, peripheral.state == .connected,
              let haptic, hapticAvailable, haptic.properties.contains(.write) else { completion(); return }
        pendingHapticCompletions.append(completion)
        peripheral.writeValue(command, for: haptic, type: .withResponse)
        armHapticWatchdog()
    }
    func disconnect() {
        wanted = false; stopScan(); cancelDiscoveryRecovery(); finishPendingHaptics()
        if let peripheral { manager.cancelPeripheralConnection(peripheral) }
        UserDefaults.standard.removeObject(forKey: "pairedDevice")
    }
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            if wanted, let peripheral {
                if peripheral.state == .connected { configure(peripheral); return }
                if peripheral.state == .connecting { return }
            }
            status = L10n.source("Ready to pair")
            if let text = UserDefaults.standard.string(forKey: "pairedDevice"), let id = UUID(uuidString: text),
               let known = central.retrievePeripherals(withIdentifiers: [id]).first { connect(known) }
        case .unauthorized: lostConnection(); status = L10n.source("Bluetooth permission denied. Enable access in iPhone Settings.")
        case .poweredOff: lostConnection(); status = L10n.source("Bluetooth is off")
        case .unsupported: lostConnection(); status = L10n.source("Bluetooth is unavailable")
        default: lostConnection(); status = L10n.source("Bluetooth resetting")
        }
    }
    func centralManager(_ central: CBCentralManager, willRestoreState dict: [String: Any]) {
        let savedID = UserDefaults.standard.string(forKey: "pairedDevice")
        let candidates = (dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral]) ?? []
        for device in candidates where device.identifier.uuidString != savedID { central.cancelPeripheralConnection(device) }
        guard let restored = candidates.first(where: { $0.identifier.uuidString == savedID }) else { return }
        peripheral = restored; restored.delegate = self
        // Restored subscriptions may still stream before codec discovery finishes.
        for service in restored.services ?? [] {
            for characteristic in service.characteristics ?? [] where characteristic.uuid == Self.audioID {
                if !captureEnabled { subscribe(false, to: characteristic) }
            }
        }
        if restored.state == .connected { configure(restored) }
        else if restored.state == .disconnected { central.connect(restored) }
    }
    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let services = (advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? [])
            + (advertisementData[CBAdvertisementDataOverflowServiceUUIDsKey] as? [CBUUID] ?? [])
        guard OmiDeviceDiscovery.matches(localName: advertisementData[CBAdvertisementDataLocalNameKey] as? String,
                                         peripheralName: peripheral.name, serviceUUIDs: services.map(\.uuidString)) else { return }
        if !devices.contains(where: { $0.identifier == peripheral.identifier }) { devices.append(peripheral) }
    }
    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        guard self.peripheral?.identifier == peripheral.identifier, wanted else { central.cancelPeripheralConnection(peripheral); return }
        configure(peripheral)
    }
    private func configure(_ device: CBPeripheral) {
        if connected, peripheral?.identifier == device.identifier {
            // Cached services can already exist while their discovery callbacks have stalled.
            consumeCachedCapabilities(device)
            finishDiscoveryIfReady()
            return
        }
        clearCapabilities(); capabilityFailure = false
        peripheral = device; device.delegate = self; connected = true
        name = device.name ?? "Omi"; status = L10n.source("Reading device capabilities")
        discoveryRecovery.start(at: ProcessInfo.processInfo.systemUptime)
        discoverCapabilities(device)
        armDiscoveryWatchdog()
    }
    private var initialSetupReady: Bool {
        audio != nil && codec != nil && (!captureEnabled || audio?.isNotifying == true) && buttonAvailable
    }
    private func discoverCapabilities(_ device: CBPeripheral) {
        // A full rediscovery recovered the verified connected-but-uninitialized hardware state.
        device.discoverServices(nil)
        consumeCachedCapabilities(device)
    }
    private func consumeCachedCapabilities(_ device: CBPeripheral) {
        for service in device.services ?? [] {
            if service.characteristics?.isEmpty == false {
                consumeCharacteristics(device, service: service, authoritative: false)
            }
            discoverCharacteristics(device, service: service)
        }
    }
    private func discoverCharacteristics(_ device: CBPeripheral, service: CBService) {
        guard !capabilityFailure, !pendingCharacteristicDiscovery.contains(service.uuid) else { return }
        pendingCharacteristicDiscovery.insert(service.uuid)
        device.discoverCharacteristics(nil, for: service)
    }
    private func cancelDiscoveryRecovery() {
        discoveryWatchdog?.cancel(); discoveryWatchdog = nil; discoveryRecovery.cancel()
    }
    private func finishDiscoveryIfReady() {
        if initialSetupReady { cancelDiscoveryRecovery() }
    }
    private func armDiscoveryWatchdog() {
        discoveryWatchdog?.cancel(); discoveryWatchdog = nil
        guard let deadline = discoveryRecovery.retryAt, let peripheral else { return }
        let deviceID = peripheral.identifier
        let delay = max(0.01, deadline - ProcessInfo.processInfo.systemUptime)
        discoveryWatchdog = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(delay)) } catch { return }
            guard let self, wanted, connected, !capabilityFailure,
                  let peripheral = self.peripheral, peripheral.identifier == deviceID,
                  peripheral.state == .connected else { return }
            discoveryWatchdog = nil
            switch discoveryRecovery.advance(at: ProcessInfo.processInfo.systemUptime, ready: initialSetupReady) {
            case .retry:
                // Retry only incomplete work. Healthy audio and its decoder remain untouched.
                if codec == nil { pendingReads.remove(Self.codecID) }
                if audio?.isNotifying != true { pendingSubscriptions.remove(Self.audioID) }
                if !buttonAvailable { pendingSubscriptions.remove(Self.buttonID) }
                pendingCharacteristicDiscovery.removeAll()
                discoverCapabilities(peripheral)
            case .exhausted:
                if !buttonAvailable {
                    let detail = L10n.text("Button notifications") + ": " + L10n.text("Not discovered")
                    onError?(L10n.source("BLE subscription: \(detail)"))
                } else {
                    onError?(L10n.source("Cannot read Omi services: \(L10n.text("Not discovered"))"))
                }
            case .wait: break
            }
            armDiscoveryWatchdog()
        }
    }
    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        status = error?.localizedDescription ?? L10n.source("Could not connect"); onError?(status)
        // CoreBluetooth owns the pending reconnect even while the app is suspended.
        if wanted, central.state == .poweredOn { central.connect(peripheral) }
    }
    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        lostConnection()
        if capabilityFailure { return }
        status = wanted ? L10n.source("Disconnected; waiting for Omi") : L10n.source("Disconnected")
        if wanted, central.state == .poweredOn { central.connect(peripheral) }
    }
    private func lostConnection() {
        stopScan()
        if connected { onDisconnect?() }
        connected = false; scanning = false; clearCapabilities()
    }
    private func armHapticWatchdog() {
        guard hapticWatchdog == nil, !pendingHapticCompletions.isEmpty else { return }
        hapticWatchdog = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(3)) } catch { return }
            guard let self else { return }
            // Late acknowledgements cannot be correlated safely with another write.
            hapticFaulted = true; hapticAvailable = false
            finishPendingHaptics()
        }
    }
    private func finishPendingHaptics() {
        hapticWatchdog?.cancel(); hapticWatchdog = nil
        let completions = pendingHapticCompletions; pendingHapticCompletions.removeAll()
        for completion in completions { completion() }
    }
    private func clearCapabilities() {
        finishPendingHaptics()
        audio = nil; codec = nil; buttonAvailable = false; battery = nil
        haptic = nil; hapticAvailable = false; hapticFaulted = false
        model = L10n.source("Unknown"); firmware = L10n.source("Unknown"); pendingSubscriptions.removeAll()
        pendingReads.removeAll(); pendingCharacteristicDiscovery.removeAll(); cancelDiscoveryRecovery()
    }
    private func failCapabilities(_ message: String) {
        cancelDiscoveryRecovery(); finishPendingHaptics(); capabilityFailure = true; status = message; onError?(message)
        if let peripheral { manager.cancelPeripheralConnection(peripheral) }
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier, !capabilityFailure else { return }
        if let error { failCapabilities(L10n.source("Cannot read Omi services: \(L10n.error(error))")); return }
        guard peripheral.services?.contains(where: { $0.uuid == Self.audioService }) == true else {
            failCapabilities(L10n.source("This device does not expose the Omi audio service. Choose another device.")); return
        }
        consumeCachedCapabilities(peripheral)
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier, !capabilityFailure else { return }
        pendingCharacteristicDiscovery.remove(service.uuid)
        if let error {
            if service.uuid == Self.audioService { failCapabilities(L10n.source("Cannot read Omi audio capabilities: \(L10n.error(error))")) }
            else { onError?(L10n.error(error)) }
            return
        }
        consumeCharacteristics(peripheral, service: service, authoritative: true)
    }
    private func consumeCharacteristics(_ peripheral: CBPeripheral, service: CBService, authoritative: Bool) {
        if service.uuid == Self.audioService {
            let characteristics = service.characteristics ?? []
            guard characteristics.contains(where: { $0.uuid == Self.audioID && canNotify($0) }),
                  characteristics.contains(where: { $0.uuid == Self.codecID && $0.properties.contains(.read) }) else {
                if authoritative { failCapabilities(L10n.source("Omi requires an audio notification characteristic and a readable codec.")) }
                return
            }
        }
        for characteristic in service.characteristics ?? [] {
            switch characteristic.uuid {
            case Self.audioID: audio = characteristic
            case Self.hapticID:
                if !hapticFaulted, service.uuid == Self.hapticService, characteristic.properties.contains(.write) {
                    haptic = characteristic; hapticAvailable = true
                }
            case Self.buttonID:
                if canNotify(characteristic) {
                    buttonAvailable = characteristic.isNotifying
                    subscribe(true, to: characteristic)
                }
            case Self.codecID, CBUUID(string: "2A19"), CBUUID(string: "2A24"), CBUUID(string: "2A26"):
                let needsRead = characteristic.uuid == Self.codecID ? codec == nil
                    : characteristic.uuid == CBUUID(string: "2A19") ? battery == nil
                    : characteristic.uuid == CBUUID(string: "2A24") ? model == L10n.source("Unknown")
                    : firmware == L10n.source("Unknown")
                if needsRead, characteristic.properties.contains(.read), !pendingReads.contains(characteristic.uuid) {
                    pendingReads.insert(characteristic.uuid)
                    peripheral.readValue(for: characteristic)
                }
                if characteristic.uuid == CBUUID(string: "2A19"), canNotify(characteristic) {
                    subscribe(true, to: characteristic)
                }
            default: break
            }
        }
        updateAudioSubscription(); finishDiscoveryIfReady()
    }
    private func updateAudioSubscription() {
        guard let audio, !capabilityFailure else { return }
        // Always unsubscribe while paused, including while a restored codec read is pending.
        subscribe(captureEnabled && codec != nil, to: audio)
        if audio.isNotifying == captureEnabled { status = captureEnabled ? L10n.source("Receiving audio") : L10n.source("Capture paused") }
    }
    private func canNotify(_ characteristic: CBCharacteristic) -> Bool {
        characteristic.properties.contains(.notify) || characteristic.properties.contains(.indicate)
    }
    private func subscribe(_ enabled: Bool, to characteristic: CBCharacteristic) {
        guard let peripheral, peripheral.state == .connected, characteristic.isNotifying != enabled,
              !pendingSubscriptions.contains(characteristic.uuid) else { return }
        pendingSubscriptions.insert(characteristic.uuid)
        peripheral.setNotifyValue(enabled, for: characteristic)
    }
    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier, !capabilityFailure else { return }
        pendingSubscriptions.remove(characteristic.uuid)
        if characteristic.uuid == Self.buttonID { buttonAvailable = error == nil && characteristic.isNotifying }
        if let error {
            if characteristic.uuid == Self.audioID { failCapabilities(L10n.source("Omi audio subscription failed: \(L10n.error(error))")) }
            else { onError?(L10n.source("BLE subscription: \(L10n.error(error))")) }
        } else if characteristic.uuid == Self.audioID {
            status = characteristic.isNotifying ? L10n.source("Receiving audio") : L10n.source("Capture paused")
            updateAudioSubscription()
        }
        finishDiscoveryIfReady()
    }
    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier, characteristic.uuid == Self.hapticID, !hapticFaulted else { return }
        hapticWatchdog?.cancel(); hapticWatchdog = nil
        if let error {
            hapticFaulted = true; hapticAvailable = false
            finishPendingHaptics(); onError?(L10n.error(error))
        } else {
            if !pendingHapticCompletions.isEmpty { pendingHapticCompletions.removeFirst()() }
            armHapticWatchdog()
        }
    }
    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier, !capabilityFailure else { return }
        pendingReads.remove(characteristic.uuid)
        if let error {
            if characteristic.uuid == Self.codecID || characteristic.uuid == Self.audioID { failCapabilities(L10n.source("Omi audio read failed: \(L10n.error(error))")) }
            else { onError?(L10n.error(error)) }
            return
        }
        guard let data = characteristic.value else {
            if characteristic.uuid == Self.codecID { failCapabilities(L10n.source("Omi returned no codec")) }
            return
        }
        switch characteristic.uuid {
        case Self.audioID: if captureEnabled { onAudio?(data, Date(), ProcessInfo.processInfo.systemUptime) }
        case Self.buttonID: onButton?(data)
        case Self.codecID:
            guard let id = data.first else { failCapabilities(L10n.source("Omi returned no codec")); return }
            let changed = codec != Int(id)
            codec = Int(id)
            if changed { onCodec?(Int(id)) }
            updateAudioSubscription(); finishDiscoveryIfReady()
        case CBUUID(string: "2A19"): battery = data.first.map(Int.init)
        case CBUUID(string: "2A24"): model = String(data: data, encoding: .utf8) ?? L10n.source("Unknown")
        case CBUUID(string: "2A26"): firmware = String(data: data, encoding: .utf8) ?? L10n.source("Unknown")
        default: break
        }
    }
}
