import 'package:web/web.dart' as web;

/// Opens [url] (a download) in a new tab; the browser sends the session cookie along.
void openUrl(Uri url) => web.window.open(url.toString(), '_blank');
