import Foundation
import SmokeLib

public struct Adder {
    public static func add(_ a: Int, _ b: Int) -> Int { a + b }

    public static func describe(_ result: Int) -> String {
        SmokeFormatter.format(result, label: "sum")
    }
}
