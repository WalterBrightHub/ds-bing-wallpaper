// make-icon-photo — turn a square photo into a macOS app icon set.
//
// Applies the standard macOS icon geometry: the artwork sits in a squircle of
// 824pt inside a 1024pt transparent canvas (Apple's ~80% rule), with a corner
// radius of 22.45% of the artwork. The source image is drawn aspect-filled.
//
// Usage: make-icon-photo <source.png> <out.iconset>
import AppKit
import Foundation

func fail(_ message: String) -> Never {
    fputs(message + "\n", stderr)
    exit(1)
}

let args = CommandLine.arguments
guard args.count >= 3 else {
    fail("usage: make-icon-photo <source.png> <out.iconset>")
}

let sourceURL = URL(fileURLWithPath: args[1])
let outDir = args[2]

guard let source = NSImage(contentsOf: sourceURL) else {
    fail("cannot read image: \(args[1])")
}
try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

let masterSize = 1024
let artInset: CGFloat = 100          // 1024 - 2*100 = 824pt artwork
let artSide = CGFloat(masterSize) - artInset * 2
let artRect = NSRect(x: artInset, y: artInset, width: artSide, height: artSide)
let cornerRadius = artSide * 0.2245

func makeBitmap(_ side: Int) -> NSBitmapImageRep {
    guard let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: side, pixelsHigh: side,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
    ) else {
        fail("cannot allocate \(side)x\(side) bitmap")
    }
    rep.size = NSSize(width: side, height: side)
    return rep
}

func fillClear(_ rect: NSRect) {
    NSColor.clear.setFill()
    rect.fill()
}

// --- master artwork at 1024pt -------------------------------------------

let master = makeBitmap(masterSize)
NSGraphicsContext.saveGraphicsState()
let masterContext = NSGraphicsContext(bitmapImageRep: master)!
NSGraphicsContext.current = masterContext
masterContext.imageInterpolation = .high

fillClear(NSRect(x: 0, y: 0, width: masterSize, height: masterSize))

let squircle = NSBezierPath(roundedRect: artRect, xRadius: cornerRadius, yRadius: cornerRadius)
NSGraphicsContext.saveGraphicsState()
squircle.addClip()
source.draw(
    in: artRect,
    from: .zero,
    operation: .sourceOver,
    fraction: 1,
    respectFlipped: true,
    hints: nil
)
NSGraphicsContext.restoreGraphicsState()

// A hairline inner rim keeps dark photos from bleeding into dark backgrounds,
// matching the subtle edge real macOS icons have.
squircle.lineWidth = 2
NSColor(white: 1, alpha: 0.10).setStroke()
squircle.stroke()

NSGraphicsContext.restoreGraphicsState()

let masterImage = NSImage(size: NSSize(width: masterSize, height: masterSize))
masterImage.addRepresentation(master)

// --- export every required size -----------------------------------------

let variants: [(String, Int)] = [
    ("icon_16x16", 16), ("icon_16x16@2x", 32),
    ("icon_32x32", 32), ("icon_32x32@2x", 64),
    ("icon_128x128", 128), ("icon_128x128@2x", 256),
    ("icon_256x256", 256), ("icon_256x256@2x", 512),
    ("icon_512x512", 512), ("icon_512x512@2x", 1024),
]

for (name, side) in variants {
    let rep = makeBitmap(side)
    NSGraphicsContext.saveGraphicsState()
    let context = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.current = context
    context.imageInterpolation = .high
    fillClear(NSRect(x: 0, y: 0, width: side, height: side))
    masterImage.draw(
        in: NSRect(x: 0, y: 0, width: side, height: side),
        from: NSRect(x: 0, y: 0, width: masterSize, height: masterSize),
        operation: .sourceOver,
        fraction: 1,
        respectFlipped: true,
        hints: nil
    )
    NSGraphicsContext.restoreGraphicsState()

    guard let data = rep.representation(using: .png, properties: [:]) else {
        fail("cannot encode \(name)")
    }
    do {
        try data.write(to: URL(fileURLWithPath: "\(outDir)/\(name).png"))
    } catch {
        fail("cannot write \(name): \(error)")
    }
}

print("photo icon written to \(outDir)")
