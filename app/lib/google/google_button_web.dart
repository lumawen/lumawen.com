import 'dart:async';

import 'package:flutter/material.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:google_sign_in_web/web_only.dart' as gsi;

/// Google's rendered button (required on the web). Gives the ID token to [onIdToken], which
/// the API checks with Google before signing in.
class GoogleButton extends StatefulWidget {
  const GoogleButton({super.key, required this.clientId, required this.onIdToken});
  final String clientId;
  final ValueChanged<String> onIdToken;

  @override
  State<GoogleButton> createState() => _GoogleButtonState();
}

Future<void>? _initialized;

class _GoogleButtonState extends State<GoogleButton> {
  StreamSubscription<GoogleSignInAuthenticationEvent>? _events;
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    _initialized ??= GoogleSignIn.instance.initialize(clientId: widget.clientId);
    _initialized!.then(
      (_) {
        if (!mounted) return;
        _events = GoogleSignIn.instance.authenticationEvents.listen((event) {
          if (event is GoogleSignInAuthenticationEventSignIn) {
            final token = event.user.authentication.idToken;
            if (token != null) widget.onIdToken(token);
          }
        });
        setState(() => _ready = true);
      },
      onError: (Object e) {
        _initialized = null; // try again next time the sign-in page opens
        debugPrint('Google sign-in unavailable: $e');
      },
    );
  }

  @override
  void dispose() {
    _events?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_ready) return const SizedBox(height: 44);
    return SizedBox(
      height: 44,
      child: gsi.renderButton(
        configuration: gsi.GSIButtonConfiguration(
          theme: gsi.GSIButtonTheme.outline,
          shape: gsi.GSIButtonShape.pill,
          text: gsi.GSIButtonText.continueWith,
          minimumWidth: 280,
        ),
      ),
    );
  }
}
