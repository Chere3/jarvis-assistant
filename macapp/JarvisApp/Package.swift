// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "JarvisApp",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "JarvisApp", path: "Sources/JarvisApp",
                          linkerSettings: [.linkedFramework("Carbon"), .linkedFramework("AppKit")])
    ]
)
