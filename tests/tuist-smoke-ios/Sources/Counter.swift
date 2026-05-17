import Foundation

public final class Counter {
    private(set) var value: Int = 0

    public init(start: Int = 0) {
        value = start
    }

    public func increment(by amount: Int = 1) {
        value += amount
    }
}
