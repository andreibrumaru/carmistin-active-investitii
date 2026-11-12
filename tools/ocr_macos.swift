// OCR local pentru macOS (framework-ul Vision): PDF sau imagine -> text, cu marcaje [pagina N].
// Compilare: swiftc -O tools/ocr_macos.swift -o tools/ocr_macos
// Utilizare: tools/ocr_macos fisier.pdf   |   tools/ocr_macos --limbi
import Foundation
import PDFKit
import Vision
import AppKit

func recunoaste(_ img: CGImage) -> String {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = true
    let dorite = ["ro-RO", "en-US"]
    if let sup = try? req.supportedRecognitionLanguages() {
        let alese = dorite.filter { sup.contains($0) }
        if !alese.isEmpty { req.recognitionLanguages = alese }
    }
    let h = VNImageRequestHandler(cgImage: img, options: [:])
    try? h.perform([req])
    let obs = (req.results ?? []).sorted { a, b in
        abs(a.boundingBox.midY - b.boundingBox.midY) > 0.01 ? a.boundingBox.midY > b.boundingBox.midY : a.boundingBox.minX < b.boundingBox.minX
    }
    return obs.compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
}

let args = CommandLine.arguments
if args.count > 1 && args[1] == "--limbi" {
    print((try? VNRecognizeTextRequest().supportedRecognitionLanguages()) ?? [])
    exit(0)
}
guard args.count > 1 else { FileHandle.standardError.write("fișier lipsă\n".data(using: .utf8)!); exit(2) }
let url = URL(fileURLWithPath: args[1])
if url.pathExtension.lowercased() == "pdf" {
    guard let doc = PDFDocument(url: url) else { exit(3) }
    for i in 0..<doc.pageCount {
        guard let pag = doc.page(at: i) else { continue }
        let r = pag.bounds(for: .mediaBox)
        let scala: CGFloat = 2.5
        let w = Int(r.width * scala), hgt = Int(r.height * scala)
        let cs = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(data: nil, width: w, height: hgt, bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { continue }
        ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: hgt))
        ctx.scaleBy(x: scala, y: scala)
        pag.draw(with: .mediaBox, to: ctx)
        if let img = ctx.makeImage() { print("[pagina \(i + 1)]\n" + recunoaste(img)) }
    }
} else if let ns = NSImage(contentsOf: url), let img = ns.cgImage(forProposedRect: nil, context: nil, hints: nil) {
    print("[pagina 1]\n" + recunoaste(img))
}
