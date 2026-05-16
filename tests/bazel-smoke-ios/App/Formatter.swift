import Foundation

public enum SmokeFormatter {
    public static func format(_ count: Int, label: String) -> String {
        return "\(label): \(count)"
    }
}
