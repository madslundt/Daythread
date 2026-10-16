import Foundation

/// Keeps superseded asynchronous reads and their cleanup from updating a newer view.
public struct RequestEpoch {
    private var current = UUID()
    public init() {}
    @discardableResult public mutating func replace() -> UUID {
        current = UUID()
        return current
    }
    public func accepts(_ token: UUID) -> Bool { token == current }
}
