import SwiftUI
import UIKit
import WebKit

@main
struct PCRemoteApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

/// "192.168.1.5" -> http://192.168.1.5:8765/
func makeURL(_ raw: String) -> URL? {
    var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    if s.isEmpty { return nil }
    if !s.contains("://") { s = "http://" + s }
    guard var c = URLComponents(string: s), let host = c.host, !host.isEmpty else { return nil }
    if c.port == nil { c.port = 8765 }
    c.path = "/"
    c.query = nil
    c.fragment = nil
    return c.url
}

struct RootView: View {
    @AppStorage("host") private var host = ""
    @State private var connected = false
    @State private var started = false

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if connected, let url = makeURL(host) {
                RemoteWebView(url: url, onChangeHost: { connected = false })
                    .ignoresSafeArea()
            } else {
                ConnectView(host: $host, onConnect: { connected = true })
            }
        }
        .preferredColorScheme(.dark)
        .onAppear {
            if !started {
                started = true
                if makeURL(host) != nil { connected = true }
            }
        }
    }
}

struct ConnectView: View {
    @Binding var host: String
    var onConnect: () -> Void
    @State private var text = ""
    @State private var error = ""

    var body: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "desktopcomputer")
                .font(.system(size: 64))
                .foregroundColor(.blue)
            Text("ПК Пульт").font(.largeTitle).bold()
            Text("Запустите start.bat на компьютере и введите адрес, который показан в его окне")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
            TextField("192.168.1.5:8765", text: $text)
                .keyboardType(.numbersAndPunctuation)
                .textInputAutocapitalization(.never)
                .disableAutocorrection(true)
                .font(.title3)
                .multilineTextAlignment(.center)
                .padding(14)
                .background(Color.white.opacity(0.08))
                .cornerRadius(14)
                .submitLabel(.go)
                .onSubmit(connect)
            Button(action: connect) {
                Text("Подключиться").bold().frame(maxWidth: .infinity).padding(14)
            }
            .background(Color.blue)
            .foregroundColor(.white)
            .cornerRadius(14)
            if !error.isEmpty {
                Text(error).foregroundColor(.red).font(.footnote)
            }
            Text("Телефон и компьютер должны быть в одной сети Wi-Fi")
                .font(.footnote)
                .foregroundColor(.secondary)
            Spacer()
            Spacer()
        }
        .padding(24)
        .frame(maxWidth: 420)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear { text = host }
    }

    private func connect() {
        guard makeURL(text) != nil else {
            error = "Неверный адрес"
            return
        }
        error = ""
        host = text.trimmingCharacters(in: .whitespacesAndNewlines)
        onConnect()
    }
}

struct RemoteWebView: UIViewRepresentable {
    let url: URL
    let onChangeHost: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()
        cfg.allowsInlineMediaPlayback = true
        let ucc = cfg.userContentController
        ucc.add(context.coordinator, name: "native")
        ucc.addUserScript(WKUserScript(source: Coordinator.injectJS,
                                       injectionTime: .atDocumentEnd,
                                       forMainFrameOnly: true))
        let wv = WKWebView(frame: .zero, configuration: cfg)
        wv.navigationDelegate = context.coordinator
        wv.uiDelegate = context.coordinator
        wv.isOpaque = false
        wv.backgroundColor = .black
        wv.scrollView.backgroundColor = .black
        wv.scrollView.isScrollEnabled = false
        wv.scrollView.bounces = false
        wv.scrollView.contentInsetAdjustmentBehavior = .never
        wv.allowsLinkPreview = false
        context.coordinator.webView = wv
        wv.load(URLRequest(url: url))
        return wv
    }

    func updateUIView(_ wv: WKWebView, context: Context) {}

    static func dismantleUIView(_ wv: WKWebView, coordinator: Coordinator) {
        wv.configuration.userContentController.removeScriptMessageHandler(forName: "native")
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
        let parent: RemoteWebView
        weak var webView: WKWebView?

        init(_ parent: RemoteWebView) { self.parent = parent }

        // Добавляет в веб-пульт кнопку «Другой ПК» (в настройках и на экране PIN).
        static let injectJS = """
        (function(){
          function post(m){try{window.webkit.messageHandlers.native.postMessage(m)}catch(e){}}
          var row=document.querySelector('#settings .row');
          if(row&&!document.getElementById('bHost')){
            var b=document.createElement('button');b.id='bHost';b.textContent='🖥 Другой ПК';
            b.onclick=function(){post('change')};row.insertBefore(b,row.firstChild);
          }
          var c=document.querySelector('.c');
          if(c&&!document.getElementById('bHost2')){
            var a=document.createElement('button');a.id='bHost2';a.textContent='Сменить адрес компьютера';
            a.style.background='#1f2430';a.onclick=function(){post('change')};c.appendChild(a);
          }
        })();
        """

        func userContentController(_ ucc: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let s = message.body as? String else { return }
            if s == "change" {
                parent.onChangeHost()
            } else if s == "retry" {
                webView?.load(URLRequest(url: parent.url))
            }
        }

        func webView(_ wv: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            showError(wv, error)
        }

        func webView(_ wv: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            showError(wv, error)
        }

        private func esc(_ s: String) -> String {
            s.replacingOccurrences(of: "&", with: "&amp;")
                .replacingOccurrences(of: "<", with: "&lt;")
                .replacingOccurrences(of: ">", with: "&gt;")
        }

        private func showError(_ wv: WKWebView, _ error: Error) {
            if (error as NSError).code == NSURLErrorCancelled { return }
            let html = """
            <!DOCTYPE html><html><head><meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
            <style>body{margin:0;background:#0b0d12;color:#e8ecf3;font:16px -apple-system,sans-serif;text-align:center;padding:22vh 24px 0}
            h2{margin:0 0 10px}p{color:#8a93a6;font-size:14px;line-height:1.5}
            button{display:block;width:100%;max-width:340px;margin:12px auto 0;padding:15px;border:0;border-radius:14px;font-size:17px;color:#fff;background:#2f6bff}
            .s{background:#1f2430}</style></head><body>
            <h2>Нет связи с компьютером</h2>
            <p>\(esc(parent.url.absoluteString))<br>\(esc(error.localizedDescription))</p>
            <p>Проверьте, что на ПК запущен start.bat, а телефон в той же сети Wi-Fi.<br>
            Если iPhone спрашивал доступ к «локальной сети», разрешите его:<br>Настройки → Конфиденциальность → Локальная сеть.</p>
            <button onclick="window.webkit.messageHandlers.native.postMessage('retry')">Повторить</button>
            <button class="s" onclick="window.webkit.messageHandlers.native.postMessage('change')">Сменить адрес</button>
            </body></html>
            """
            wv.loadHTMLString(html, baseURL: nil)
        }

        // alert() и confirm() из веб-пульта (например, «Выключить компьютер?»)
        func webView(_ wv: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
            let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            a.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
            show(a, fallback: completionHandler)
        }

        func webView(_ wv: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
            let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            a.addAction(UIAlertAction(title: "Отмена", style: .cancel) { _ in completionHandler(false) })
            a.addAction(UIAlertAction(title: "Да", style: .destructive) { _ in completionHandler(true) })
            show(a, fallback: { completionHandler(false) })
        }

        private func show(_ alert: UIAlertController, fallback: () -> Void) {
            let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
            var vc = scene?.windows.first(where: { $0.isKeyWindow })?.rootViewController
                ?? scene?.windows.first?.rootViewController
            while let p = vc?.presentedViewController { vc = p }
            guard let top = vc else { fallback(); return }
            top.present(alert, animated: true)
        }
    }
}
