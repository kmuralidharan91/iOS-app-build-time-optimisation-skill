import ProjectDescription

let project = Project(
    name: "TuistSmoke",
    targets: [
        .target(
            name: "TuistSmoke",
            destinations: .iOS,
            product: .framework,
            bundleId: "io.tuist.TuistSmoke",
            deploymentTargets: .iOS("17.0"),
            infoPlist: .default,
            sources: ["Sources/**"]
        )
    ]
)
