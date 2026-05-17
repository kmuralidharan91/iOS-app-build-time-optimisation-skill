import Foundation

public final class Counter {
    public private(set) var value: Int

    public init(start: Int = 0) {
        self.value = start
    }

    public func increment(by delta: Int = 1) {
        value += delta
    }
}
