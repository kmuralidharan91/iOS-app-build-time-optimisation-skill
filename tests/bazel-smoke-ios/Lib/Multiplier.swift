import Foundation

public struct Multiplier {
    public static func multiply(_ a: Int, _ b: Int) -> Int { a * b }

    public static func factorial(_ n: Int) -> Int {
        n <= 1 ? 1 : (2...n).reduce(1, *)
    }
}
