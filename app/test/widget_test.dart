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
        '/api/admin/users' => {
          'total': 1,
          'users': [admin],
        },
        _ => {'_status': 404},
      },
    );
    await tester.pumpWidget(LumawenApp(api: api));
    await tester.pumpAndSettle();
    expect(find.text('Newsletters'), findsWidgets);
    expect(find.text('Mailing list'), findsOneWidget);
    expect(find.textContaining('Nothing written yet'), findsOneWidget);
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
