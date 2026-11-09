import 'package:flutter/material.dart';

import 'api.dart';
import 'screens/account.dart';
import 'screens/home.dart';
import 'theme.dart';

void main() => runApp(LumawenApp(api: Api()));

/// The dashboard. Routes live after the # (e.g. /dashboard/#/confirm?token=...), so tokens
/// from email links never reach server logs.
class LumawenApp extends StatelessWidget {
  const LumawenApp({super.key, required this.api});
  final Api api;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Lumawen',
      debugShowCheckedModeBanner: false,
      theme: lumawenTheme(),
      // One opening route, not "/" underneath it: otherwise the home screen starts too and
      // sends signed-out visitors of /confirm or /reset links to sign-in.
      onGenerateInitialRoutes: (name) => [_route(RouteSettings(name: name))],
      onGenerateRoute: _route,
    );
  }

  Route<void> _route(RouteSettings settings) {
    final uri = Uri.parse(settings.name ?? '/');
    final token = uri.queryParameters['token'] ?? '';
    final Widget page = switch (uri.path) {
      '/register' => RegisterScreen(api: api),
      '/confirm' => ConfirmScreen(api: api, token: token),
      '/forgot' => ForgotScreen(api: api),
      '/reset' => ResetScreen(api: api, token: token),
      '/login' => LoginScreen(api: api),
      _ => HomeScreen(api: api),
    };
    return MaterialPageRoute(settings: settings, builder: (_) => page);
  }
}
