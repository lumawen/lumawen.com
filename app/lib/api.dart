// Talks to the Lumawen API. On the web dashboard the API is on the same site (/api/), and
// signing in sets an HttpOnly session cookie that the browser sends along by itself.
import 'dart:convert';

import 'package:http/http.dart' as http;

class ApiError implements Exception {
  ApiError(this.status, this.message);
  final int status;
  final String message;

  @override
  String toString() => message;
}

class User {
  User.fromJson(Map<String, dynamic> j)
    : id = j['id'] as String,
      email = j['email'] as String,
      displayName = j['display_name'] as String?,
      role = j['role'] as String;
  final String id;
  final String email;
  final String? displayName;
  final String role;
  bool get isAdmin => role == 'admin';
}

class Subscriber {
  Subscriber.fromJson(Map<String, dynamic> j)
    : id = j['id'] as String,
      email = j['email'] as String,
      status = j['status'] as String,
      createdAt = DateTime.parse(j['created_at'] as String).toLocal();
  final String id;
  final String email;
  final String status;
  final DateTime createdAt;
}

class SubscriberPage {
  SubscriberPage.fromJson(Map<String, dynamic> j)
    : total = j['total'] as int,
      counts = (j['counts'] as Map).cast<String, int>(),
      subscribers = [
        for (final s in j['subscribers'] as List) Subscriber.fromJson(s as Map<String, dynamic>),
      ];
  final int total;
  final Map<String, int> counts;
  final List<Subscriber> subscribers;
}

class Newsletter {
  Newsletter.fromJson(Map<String, dynamic> j)
    : id = j['id'] as String,
      subject = j['subject'] as String,
      bodyText = j['body_text'] as String,
      status = j['status'] as String,
      sendAt = _date(j['send_at']),
      queuedAt = _date(j['queued_at']),
      recipients = j['recipients'] as int?,
      pending = (j['delivery'] as Map)['pending'] as int,
      sent = (j['delivery'] as Map)['sent'] as int,
      failed = (j['delivery'] as Map)['failed'] as int,
      updatedAt = DateTime.parse(j['updated_at'] as String).toLocal();
  final String id;
  final String subject;
  final String bodyText;
  final String status;
  final DateTime? sendAt;
  final DateTime? queuedAt;
  final int? recipients;
  final int pending;
  final int sent;
  final int failed;
  final DateTime updatedAt;

  static DateTime? _date(Object? v) => v == null ? null : DateTime.parse(v as String).toLocal();
}

class Api {
  Api({Uri? base, http.Client? client})
    : base = base ?? Uri.base.resolve('/api/'),
      _http = client ?? http.Client();

  final Uri base;
  final http.Client _http;

  Uri url(String path, [Map<String, String>? query]) =>
      base.resolve(path).replace(queryParameters: query == null || query.isEmpty ? null : query);

  Future<dynamic> _send(
    String method,
    String path, {
    Object? body,
    Map<String, String>? query,
  }) async {
    final request = http.Request(method, url(path, query));
    if (body != null) {
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode(body);
    }
    final response = await http.Response.fromStream(await _http.send(request));
    final text = utf8.decode(response.bodyBytes);
    if (response.statusCode >= 400) {
      throw ApiError(response.statusCode, _message(response.statusCode, text));
    }
    return text.isEmpty ? null : jsonDecode(text);
  }

  static String _message(int status, String text) {
    try {
      final detail = (jsonDecode(text) as Map)['detail'];
      if (detail is String) return detail;
      if (detail is List && detail.isNotEmpty) {
        final msg = (detail.first as Map)['msg'] as String? ?? 'Please check what you entered.';
        return msg.replaceFirst('Value error, ', '');
      }
    } catch (_) {}
    return switch (status) {
      401 => 'Please sign in.',
      403 => 'You don\'t have access to that.',
      429 => 'Too many tries. Please wait a few minutes.',
      _ => 'Something went wrong ($status). Please try again.',
    };
  }

  // ---- accounts --------------------------------------------------------------------------

  Future<String?> googleClientId() async =>
      (await _send('GET', 'config') as Map)['google_client_id'] as String?;

  Future<User?> me() async {
    try {
      return User.fromJson(await _send('GET', 'me') as Map<String, dynamic>);
    } on ApiError catch (e) {
      if (e.status == 401) return null;
      rethrow;
    }
  }

  Future<User> login(String email, String password) async {
    final r = await _send('POST', 'auth/login', body: {'email': email, 'password': password});
    return User.fromJson((r as Map)['user'] as Map<String, dynamic>);
  }

  Future<User> google(String idToken) async {
    final r = await _send('POST', 'auth/google', body: {'id_token': idToken});
    return User.fromJson((r as Map)['user'] as Map<String, dynamic>);
  }

  Future<void> logout() => _send('POST', 'auth/logout');

  Future<void> register(String email, String password) =>
      _send('POST', 'auth/register', body: {'email': email, 'password': password});

  /// Confirms the email and signs in.
  Future<User> confirm(String token, String password) async {
    final r = await _send('POST', 'auth/confirm', body: {'token': token, 'password': password});
    return User.fromJson((r as Map)['user'] as Map<String, dynamic>);
  }

  Future<void> forgotPassword(String email) =>
      _send('POST', 'auth/forgot-password', body: {'email': email});

  Future<void> resetPassword(String token, String password) =>
      _send('POST', 'auth/reset-password', body: {'token': token, 'password': password});

  // ---- admin: users and mailing list -------------------------------------------------------

  Future<(int, List<User>)> users({int offset = 0}) async {
    final r = await _send('GET', 'admin/users', query: {'offset': '$offset'}) as Map;
    return (
      r['total'] as int,
      [for (final u in r['users'] as List) User.fromJson(u as Map<String, dynamic>)],
    );
  }

  Future<SubscriberPage> subscribers({String? status, String? q, int offset = 0}) async =>
      SubscriberPage.fromJson(
        await _send(
          'GET',
          'admin/subscribers',
          query: {'status': ?status, if (q != null && q.isNotEmpty) 'q': q, 'offset': '$offset'},
        ) as Map<String, dynamic>,
      );

  Uri subscribersCsv() => url('admin/subscribers.csv', {'status': 'all'});

  Future<void> deleteSubscriber(String id) => _send('DELETE', 'admin/subscribers/$id');

  // ---- admin: newsletters ------------------------------------------------------------------

  Future<List<Newsletter>> newsletters() async {
    final r = await _send('GET', 'admin/newsletters') as Map;
    return [
      for (final n in r['newsletters'] as List) Newsletter.fromJson(n as Map<String, dynamic>),
    ];
  }

  Future<Newsletter> _newsletter(String method, String path, [Object? body]) async =>
      Newsletter.fromJson(await _send(method, path, body: body) as Map<String, dynamic>);

  Future<Newsletter> createNewsletter(String subject, String body) =>
      _newsletter('POST', 'admin/newsletters', {'subject': subject, 'body_text': body});

  Future<Newsletter> updateNewsletter(String id, String subject, String body) =>
      _newsletter('PATCH', 'admin/newsletters/$id', {'subject': subject, 'body_text': body});

  Future<void> deleteNewsletter(String id) => _send('DELETE', 'admin/newsletters/$id');

  Future<Newsletter> sendTest(String id) => _newsletter('POST', 'admin/newsletters/$id/test', {});

  Future<Newsletter> schedule(String id, DateTime? sendAt) => _newsletter(
    'POST',
    'admin/newsletters/$id/schedule',
    {if (sendAt != null) 'send_at': sendAt.toUtc().toIso8601String()},
  );

  Future<Newsletter> cancel(String id) => _newsletter('POST', 'admin/newsletters/$id/cancel');
}
