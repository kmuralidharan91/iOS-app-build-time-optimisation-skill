// swift-tools-version: 5.10
import PackageDescription

// LocalPkg — small local SPM module bundled alongside the Bazel
// project so bazel_adapter.package_graph() has a Package.swift to
// surface (exercises the analyzer's spm_graph rule family on a Bazel
// build). The module does not participate in the Bazel build; it's
// fixture-only.
let package = Package(
    name: "LocalPkg",
    platforms: [.iOS(.v17)],
    products: [
        .library(name: "LocalPkg", targets: ["LocalPkg"]),
    ],
    dependencies: [
        // swift-syntax pin is what F6 (spm/swift-syntax-not-prebuilt)
        // matches. The resolved pin lives in Package.resolved (next to
        // this manifest) so the analyzer fires its informational
        // finding.
        .package(url: "https://github.com/swiftlang/swift-syntax.git", from: "510.0.0"),
    ],
    targets: [
        .target(
            name: "LocalPkg",
            dependencies: [
                .product(name: "SwiftSyntax", package: "swift-syntax"),
            ],
            path: "Sources/LocalPkg"
        ),
    ]
)
