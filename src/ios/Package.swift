// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "OmiCore",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "OmiCore", targets: ["OmiCore"])],
    dependencies: [
        .package(url: "https://github.com/alta/swift-opus.git", revision: "6f3cb6bd3ffed1fe5f06d00a962d5c191a50daf8")
    ],
    targets: [
        .target(name: "OmiCore", dependencies: [.product(name: "Copus", package: "swift-opus")]),
        .testTarget(name: "OmiCoreTests", dependencies: ["OmiCore", .product(name: "Copus", package: "swift-opus")])
    ]
)
