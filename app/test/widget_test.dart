import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lumawen_app/api.dart';
import 'package:lumawen_app/main.dart';

Api fakeApi(Map<String, Object?> Function(http.Request) respond) => Api(
  base: Uri.parse('https://test.lumawen.com/api/'),
  client: MockClient((request) async {
    final body = Map.of(respond(request));
    final status = body['_status'] as int? ?? 200;
    return http.Response(
      jsonEncode(body..remove('_status')),
      status,
      headers: {'content-type': 'application/json'},
    );
  }),
);

const admin = {
  'id': '00000000-0000-0000-0000-000000000001',
  'email': 'admin@lumawen.com',
  'display_name': null,
  'role': 'admin',
  'email_verified': true,
  'created_at': '2026-11-09T00:00:00Z',
};

const player = {
  'id': '00000000-0000-0000-0000-000000000002',
  'email': 'player@example.com',
  'display_name': null,
  'role': 'user',
  'email_verified': true,
  'created_at': '2026-11-09T00:00:00Z',
};

Map<String, Object?> member(Map<String, Object?> user, {String? suspendedAt}) => {
  ...user,
  'suspended_at': suspendedAt,
  'suspended_reason': null,
  'last_seen_at': null,
  'has_password': true,
  'has_google': false,
  'sessions': 1,
};

Map<String, Object?> userPage(List<Map<String, Object?>> users) => {
  'total': users.length,
  'counts': {
    'user': 1,
    'contributor': 0,
    'moderator': 1,
    'security': 0,
    'admin': 0,
    'suspended': 0,
  },
  'users': users,
};

void main() {
  testWidgets('signed out: shows the sign-in form', (tester) async {
    final api = fakeApi(
      (r) => switch (r.url.path) {
        '/api/me' => {'_status': 401, 'detail': 'Please sign in.'},
        '/api/config' => {'google_client_id': null},
        _ => {'_status': 404},
      },
    );
    await tester.pumpWidget(LumawenApp(api: api));
    await tester.pumpAndSettle();
    expect(find.text('Sign in'), findsWidgets);
    expect(find.text('Create an account'), findsOneWidget);
  });

  testWidgets('admin: sees the dashboard tabs and newsletters', (tester) async {
    final api = fakeApi(
      (r) => switch (r.url.path) {
        '/api/me' => admin,
        '/api/admin/newsletters' => {'total': 0, 'newsletters': []},
        '/api/admin/subscribers' => {
          'total': 0,
          'counts': {'pending': 0, 'subscribed': 0, 'unsubscribed': 0},
          'subscribers': [],
        },
        '/api/admin/users' => userPage([member(admin)]),
        _ => {'_status': 404},
      },
    );
    await tester.pumpWidget(LumawenApp(api: api));
    await tester.pumpAndSettle();
    expect(find.text('Newsletters'), findsWidgets);
    expect(find.text('Mailing list'), findsOneWidget);
    expect(find.textContaining('Nothing written yet'), findsOneWidget);
  });

  testWidgets('moderator: only the Users tab, with search and suspend', (tester) async {
    final mod = {
      ...player,
      'id': '00000000-0000-0000-0000-000000000003',
      'role': 'moderator',
      'email': 'mod@example.com',
    };
    final queries = <String?>[];
    final api = fakeApi((r) {
      if (r.url.path == '/api/admin/users') queries.add(r.url.queryParameters['q']);
      return switch (r.url.path) {
        '/api/me' => mod,
        '/api/admin/users' => userPage([member(player), member(mod)]),
        _ => {'_status': 404},
      };
    });
    await tester.pumpWidget(LumawenApp(api: api));
    await tester.pumpAndSettle();
    expect(find.text('Users'), findsOneWidget);
    expect(find.text('Newsletters'), findsNothing);
    expect(find.text('Mailing list'), findsNothing);
    expect(find.text('Activity'), findsNothing);
    await tester.enterText(find.byType(TextField), 'play');
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();
    expect(queries.last, 'play');
    await tester.tap(find.text('player@example.com'));
    await tester.pumpAndSettle();
    expect(find.text('Suspend'), findsOneWidget);
    expect(find.text('Delete account'), findsNothing); // admins only
    expect(find.text('Sign out everywhere'), findsNothing); // security and admins
  });

  testWidgets('contributor: can write but not send', (tester) async {
    final writer = {...player, 'role': 'contributor'};
    final api = fakeApi(
      (r) => switch (r.url.path) {
        '/api/me' => writer,
        '/api/admin/newsletters' => {'total': 0, 'newsletters': []},
        _ => {'_status': 404},
      },
    );
    await tester.pumpWidget(LumawenApp(api: api));
    await tester.pumpAndSettle();
    expect(find.text('Users'), findsNothing);
    await tester.tap(find.text('Write'));
    await tester.pumpAndSettle();
    expect(find.text('Send me a test'), findsOneWidget);
    expect(find.text('Send now'), findsNothing);
    expect(find.text('Schedule'), findsNothing);
    expect(find.textContaining('an admin schedules or sends it'), findsOneWidget);
  });

  testWidgets('signed out: an email confirm link opens the confirm screen', (tester) async {
    tester.binding.platformDispatcher.defaultRouteNameTestValue = '/confirm?token=abc123def';
    addTearDown(tester.binding.platformDispatcher.clearDefaultRouteNameTestValue);
    String? sentToken;
    final api = fakeApi((r) {
      if (r.url.path == '/api/auth/confirm') {
        sentToken = (jsonDecode(r.body) as Map)['token'] as String;
        return {'user': player};
      }
      return switch (r.url.path) {
        '/api/me' => sentToken == null ? {'_status': 401, 'detail': 'Please sign in.'} : player,
        _ => {'_status': 404},
      };
    });
    await tester.pumpWidget(LumawenApp(api: api));
    await tester.pumpAndSettle();
    expect(find.text('Confirm your account'), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'correct horse battery');
    await tester.tap(find.text('Confirm'));
    await tester.pumpAndSettle();
    expect(sentToken, 'abc123def');
    expect(find.text('Confirm your account'), findsNothing);
    expect(find.textContaining('You\'re signed in as'), findsOneWidget);
  });

  test('Google sign-in for other addresses waits for the emailed link', () async {
    final paths = <String>[];
    final api = fakeApi((r) {
      paths.add(r.url.path);
      if (r.url.path.endsWith('/google')) return {'_status': 202, 'status': 'check_email'};
      expect((jsonDecode(r.body) as Map)['token'], 'link-token');
      return {'user': admin};
    });
    expect(await api.google('id-token'), isNull);
    expect((await api.googleConfirm('link-token', 'id-token')).email, 'admin@lumawen.com');
    expect(paths, ['/api/auth/google', '/api/auth/google/confirm']);
  });

  test('API errors carry the server message', () async {
    final api = fakeApi((r) => {'_status': 409, 'detail': 'This newsletter was already sent.'});
    expect(
      () => api.cancel('x'),
      throwsA(isA<ApiError>().having((e) => e.message, 'message', contains('already sent'))),
    );
  });

  test('schedule sends a UTC time', () async {
    late Map body;
    final api = fakeApi((r) {
      body = jsonDecode(r.body) as Map;
      return {
        'id': 'n1',
        'subject': 's',
        'body_text': 'b',
        'status': 'scheduled',
        'send_at': '2026-12-01T15:00:00Z',
        'queued_at': null,
        'recipients': null,
        'delivery': {'pending': 0, 'sent': 0, 'failed': 0},
        'created_at': '2026-11-09T00:00:00Z',
        'updated_at': '2026-11-09T00:00:00Z',
      };
    });
    await api.schedule('n1', DateTime.utc(2026, 12, 1, 15));
    expect(body['send_at'], '2026-12-01T15:00:00.000Z');
  });
}
