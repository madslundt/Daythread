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
    var identifier: String { peripheral?.identifier.uuidString ?? "iphone" }
    var captureEnabled = true { didSet { updateAudioSubscription() } }
    @ObservationIgnored private var manager: CBCentralManager!
    @ObservationIgnored private var peripheral: CBPeripheral?
    @ObservationIgnored private var audio: CBCharacteristic?
    @ObservationIgnored var onAudio: ((Data, Date, TimeInterval) -> Void)?
    @ObservationIgnored var onButton: ((Data) -> Void)?
    @ObservationIgnored var onDisconnect: (() -> Void)?
    @ObservationIgnored var onCodec: ((Int) -> Void)?
    @ObservationIgnored var onError: ((String) -> Void)?
    @ObservationIgnored private var wanted = true
    @ObservationIgnored private var capabilityFailure = false
    @ObservationIgnored private var pendingSubscriptions: Set<CBUUID> = []

    override init() {
        super.init()
        manager = CBCentralManager(delegate: self, queue: .main, options: [
            CBCentralManagerOptionRestoreIdentifierKey: "com.daythread.bluetooth",
            CBCentralManagerOptionShowPowerAlertKey: true
        ])
    }
    func scan() {
        guard manager.state == .poweredOn else { status = L10n.source("Enable Bluetooth in Settings"); return }
        wanted = true; scanning = true; devices = []; status = L10n.source("Looking for Omi")
        // Foreground pairing also finds firmware that advertises a name without the audio service.
        // Reconnection uses the saved peripheral identifier, including in the background.
        manager.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
    }
    func connect(_ device: CBPeripheral) {
        wanted = true; capabilityFailure = false; manager.stopScan(); scanning = false
        if let previous = peripheral, previous.identifier != device.identifier {
            manager.cancelPeripheralConnection(previous)
            lostConnection()
        }
        peripheral = device; device.delegate = self; status = L10n.source("Connecting to \(device.name ?? "Omi")")
        UserDefaults.standard.set(device.identifier.uuidString, forKey: "pairedDevice")
        manager.connect(device, options: [CBConnectPeripheralOptionNotifyOnDisconnectionKey: true])
    }
    func disconnect() {
        wanted = false; manager.stopScan(); scanning = false
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
        guard !connected || peripheral?.identifier != device.identifier else { return }
        clearCapabilities(); capabilityFailure = false
        peripheral = device; device.delegate = self; connected = true
        name = device.name ?? "Omi"; status = L10n.source("Reading device capabilities")
        device.discoverServices([Self.audioService, Self.buttonService, CBUUID(string: "180F"), CBUUID(string: "180A")])
    }
    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        status = error?.localizedDescription ?? L10n.source("Could not connect"); onError?(status)
        if wanted { central.connect(peripheral) }
    }
    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        lostConnection()
        if capabilityFailure { return }
        status = wanted ? L10n.source("Disconnected; waiting for Omi") : L10n.source("Disconnected")
        if wanted, central.state == .poweredOn { central.connect(peripheral) }
    }
    private func lostConnection() {
        if connected { onDisconnect?() }
        connected = false; scanning = false; clearCapabilities()
    }
    private func clearCapabilities() {
        audio = nil; codec = nil; buttonAvailable = false; battery = nil
        model = L10n.source("Unknown"); firmware = L10n.source("Unknown"); pendingSubscriptions.removeAll()
    }
    private func failCapabilities(_ message: String) {
        capabilityFailure = true; status = message; onError?(message)
        if let peripheral { manager.cancelPeripheralConnection(peripheral) }
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier, !capabilityFailure else { return }
        if let error { failCapabilities(L10n.source("Cannot read Omi services: \(L10n.error(error))")); return }
        guard peripheral.services?.contains(where: { $0.uuid == Self.audioService }) == true else {
            failCapabilities(L10n.source("This device does not expose the Omi audio service. Choose another device.")); return
        }
        for service in peripheral.services ?? [] { peripheral.discoverCharacteristics(nil, for: service) }
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier, !capabilityFailure else { return }
        if let error {
            if service.uuid == Self.audioService { failCapabilities(L10n.source("Cannot read Omi audio capabilities: \(L10n.error(error))")) }
            else { onError?(L10n.error(error)) }
            return
        }
        if service.uuid == Self.audioService {
            let characteristics = service.characteristics ?? []
            guard characteristics.contains(where: { $0.uuid == Self.audioID && canNotify($0) }),
                  characteristics.contains(where: { $0.uuid == Self.codecID && $0.properties.contains(.read) }) else {
                failCapabilities(L10n.source("Omi requires an audio notification characteristic and a readable codec.")); return
            }
        }
        for characteristic in service.characteristics ?? [] {
            switch characteristic.uuid {
            case Self.audioID: audio = characteristic
            case Self.buttonID:
                if canNotify(characteristic) {
                    buttonAvailable = characteristic.isNotifying
                    subscribe(true, to: characteristic)
                }
            case Self.codecID, CBUUID(string: "2A19"), CBUUID(string: "2A24"), CBUUID(string: "2A26"):
                if characteristic.properties.contains(.read) { peripheral.readValue(for: characteristic) }
                if characteristic.uuid == CBUUID(string: "2A19"), canNotify(characteristic) {
                    subscribe(true, to: characteristic)
                }
            default: break
            }
        }
        updateAudioSubscription()
    }
    private func updateAudioSubscription() {
        guard let audio, codec != nil, !capabilityFailure else { return }
        subscribe(captureEnabled, to: audio)
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
    }
    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier, !capabilityFailure else { return }
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
            codec = Int(id); onCodec?(Int(id)); updateAudioSubscription()
        case CBUUID(string: "2A19"): battery = data.first.map(Int.init)
        case CBUUID(string: "2A24"): model = String(data: data, encoding: .utf8) ?? L10n.source("Unknown")
        case CBUUID(string: "2A26"): firmware = String(data: data, encoding: .utf8) ?? L10n.source("Unknown")
        default: break
        }
    }
}
