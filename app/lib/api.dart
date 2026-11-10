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

const roles = ['user', 'contributor', 'moderator', 'security', 'admin'];

const roleLabels = {
  'user': 'User',
  'contributor': 'Contributor',
  'moderator': 'Moderator',
  'security': 'Security',
  'admin': 'Admin',
};

/// What each role can do. Mirrors api/src/lumawen_api/auth.py (the API enforces it).
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
  bool get canWrite => role == 'admin' || role == 'contributor';
  bool get canManageUsers => const {'admin', 'security', 'moderator'}.contains(role);
  bool get canSeeActivity => role == 'admin' || role == 'security';
  bool get isStaff => canWrite || canManageUsers;
}

/// A user as staff see them in the Users tab.
class Member extends User {
  Member.fromJson(super.j)
    : createdAt = DateTime.parse(j['created_at'] as String).toLocal(),
      emailVerified = j['email_verified'] as bool,
      suspendedAt = _date(j['suspended_at']),
      suspendedReason = j['suspended_reason'] as String?,
      lastSeenAt = _date(j['last_seen_at']),
      hasPassword = j['has_password'] as bool,
      hasGoogle = j['has_google'] as bool,
      sessions = j['sessions'] as int,
      super.fromJson();
  final DateTime createdAt;
  final bool emailVerified;
  final DateTime? suspendedAt;
  final String? suspendedReason;
  final DateTime? lastSeenAt;
  final bool hasPassword;
  final bool hasGoogle;
  final int sessions;
}

class MemberPage {
  MemberPage.fromJson(Map<String, dynamic> j)
    : total = j['total'] as int,
      counts = (j['counts'] as Map).cast<String, int>(),
      users = [for (final u in j['users'] as List) Member.fromJson(u as Map<String, dynamic>)];
  final int total;
  final Map<String, int> counts;
  final List<Member> users;
}

class Activity {
  Activity.fromJson(Map<String, dynamic> j)
    : id = j['id'] as int,
      createdAt = DateTime.parse(j['created_at'] as String).toLocal(),
      actor = j['actor_email'] as String,
      action = j['action'] as String,
      target = j['target'] as String,
      detail = (j['detail'] as Map).cast<String, dynamic>();
  final int id;
  final DateTime createdAt;
  final String actor;
  final String action;
  final String target;
  final Map<String, dynamic> detail;
}

DateTime? _date(Object? v) => v == null ? null : DateTime.parse(v as String).toLocal();

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
      archivedAt = _date(j['archived_at']),
      deleteAfter = _date(j['delete_after']),
      version = j['updated_at'] as String,
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
  final DateTime? archivedAt;
  final DateTime? deleteAfter;

  /// updated_at exactly as the server sent it: proves which version you saw before sending.
  final String version;
  final DateTime updatedAt;
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

  /// Signs in, or returns null when the address isn't one Google hosts: then we emailed a
  /// link, and the person finishes with [googleConfirm] using the same Google account.
  Future<User?> google(String idToken) async {
    final r = await _send('POST', 'auth/google', body: {'id_token': idToken}) as Map;
    if (r['status'] == 'check_email') return null;
    return User.fromJson(r['user'] as Map<String, dynamic>);
  }

  Future<User> googleConfirm(String token, String idToken) async {
    final r = await _send(
      'POST',
      'auth/google/confirm',
      body: {'token': token, 'id_token': idToken},
    );
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

  Future<MemberPage> users({String? q, String? role, bool? suspended, int offset = 0}) async =>
      MemberPage.fromJson(
        await _send(
          'GET',
          'admin/users',
          query: {
            if (q != null && q.isNotEmpty) 'q': q,
            'role': ?role,
            if (suspended != null) 'suspended': '$suspended',
            'offset': '$offset',
          },
        ) as Map<String, dynamic>,
      );

  Future<Member> _member(String method, String path, [Object? body]) async =>
      Member.fromJson(await _send(method, path, body: body) as Map<String, dynamic>);

  Future<Member> setRole(String id, String role) =>
      _member('PUT', 'admin/users/$id/role', {'role': role});

  Future<Member> suspend(String id, String reason) =>
      _member('POST', 'admin/users/$id/suspend', {'reason': reason});

  Future<Member> unsuspend(String id) => _member('POST', 'admin/users/$id/unsuspend');

  Future<Member> signOutEverywhere(String id) => _member('POST', 'admin/users/$id/sign-out');

  Future<void> deleteUser(String id) => _send('DELETE', 'admin/users/$id');

  Future<(int, List<Activity>)> activity({String? q, int offset = 0}) async {
    final r = await _send(
      'GET',
      'admin/activity',
      query: {if (q != null && q.isNotEmpty) 'q': q, 'offset': '$offset'},
    ) as Map;
    return (
      r['total'] as int,
      [for (final e in r['entries'] as List) Activity.fromJson(e as Map<String, dynamic>)],
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

  Future<List<Newsletter>> newsletters({bool archived = false}) async {
    final r =
        await _send('GET', 'admin/newsletters', query: {if (archived) 'archived': 'true'}) as Map;
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

  Future<Newsletter> schedule(String id, DateTime? sendAt, {String? seenVersion}) => _newsletter(
    'POST',
    'admin/newsletters/$id/schedule',
    {if (sendAt != null) 'send_at': sendAt.toUtc().toIso8601String(), 'seen_version': ?seenVersion},
  );

  Future<Newsletter> cancel(String id) => _newsletter('POST', 'admin/newsletters/$id/cancel');

  Future<Newsletter> restore(String id) => _newsletter('POST', 'admin/newsletters/$id/restore');
}
