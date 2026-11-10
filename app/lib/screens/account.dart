// Sign in, create an account, confirm it, and reset a forgotten password.
import 'package:flutter/material.dart';

import '../api.dart';
import '../google/google_button.dart';
import '../theme.dart';

/// A narrow centered column with the mark on top, used by every account screen.
class AuthFrame extends StatelessWidget {
  const AuthFrame({super.key, required this.title, this.subtitle, required this.children});
  final String title;
  final String? subtitle;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 40),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 400),
              child: AutofillGroup(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Center(child: Mark(height: 48)),
                    const SizedBox(height: 24),
                    Text(title, textAlign: TextAlign.center, style: theme.textTheme.headlineMedium),
                    if (subtitle != null) ...[
                      const SizedBox(height: 8),
                      Text(
                        subtitle!,
                        textAlign: TextAlign.center,
                        style: theme.textTheme.bodyLarge?.copyWith(
                          color: Colors.white.withValues(alpha: .66),
                        ),
                      ),
                    ],
                    const SizedBox(height: 28),
                    ...children,
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Runs [action] while showing a spinner; turns API errors into a message.
mixin Busy<T extends StatefulWidget> on State<T> {
  bool busy = false;
  String? error;

  Future<void> run(Future<void> Function() action) async {
    if (busy) return; // e.g. Enter pressed again while signing in
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await action();
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } catch (_) {
      if (mounted) setState(() => error = 'Can\'t reach Lumawen right now. Please try again.');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Widget errorText() => error == null
      ? const SizedBox.shrink()
      : Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Text(error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
        );

  Widget submit(String label, VoidCallback onPressed) => FilledButton(
    onPressed: busy ? null : onPressed,
    child: busy
        ? const SizedBox(height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
        : Text(label),
  );
}

String? _emailError(String? v) =>
    v != null && RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(v.trim())
    ? null
    : 'Enter a valid email address.';

String? _passwordError(String? v) =>
    v != null && v.length >= 10 ? null : 'Use at least 10 characters.';

const _gap = SizedBox(height: 14);

void _goHome(BuildContext context) =>
    Navigator.of(context).pushNamedAndRemoveUntil('/', (_) => false);

// ---- sign in -----------------------------------------------------------------------------

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.api});
  final Api api;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> with Busy {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  String? _googleClientId;
  bool _checkEmail = false;

  @override
  void initState() {
    super.initState();
    widget.api.googleClientId().then((id) {
      if (mounted) setState(() => _googleClientId = id);
    }, onError: (_) {});
  }

  void _login() {
    if (!_form.currentState!.validate()) return;
    run(() async {
      await widget.api.login(_email.text.trim(), _password.text);
      if (mounted) _goHome(context);
    });
  }

  void _google(String idToken) => run(() async {
    final user = await widget.api.google(idToken);
    if (!mounted) return;
    if (user == null) {
      setState(() => _checkEmail = true);
    } else {
      _goHome(context);
    }
  });

  @override
  Widget build(BuildContext context) {
    if (_checkEmail) {
      return const AuthFrame(
        title: 'Check your email',
        subtitle:
            'We sent a link to the address on your Google account. Open it and continue with '
            'the same Google account to finish signing in. It works for 24 hours.',
        children: [],
      );
    }
    return AuthFrame(
      title: 'Sign in',
      children: [
        if (_googleClientId != null) ...[
          Center(
            child: GoogleButton(clientId: _googleClientId!, onIdToken: _google),
          ),
          const SizedBox(height: 20),
          const _Or(),
          const SizedBox(height: 20),
        ],
        Form(
          key: _form,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextFormField(
                controller: _email,
                decoration: const InputDecoration(labelText: 'Email'),
                keyboardType: TextInputType.emailAddress,
                autofillHints: const [AutofillHints.email],
                validator: _emailError,
              ),
              _gap,
              TextFormField(
                controller: _password,
                decoration: const InputDecoration(labelText: 'Password'),
                obscureText: true,
                autofillHints: const [AutofillHints.password],
                onFieldSubmitted: (_) => _login(),
              ),
              _gap,
              errorText(),
              submit('Sign in', _login),
            ],
          ),
        ),
        const SizedBox(height: 16),
        Wrap(
          alignment: WrapAlignment.center,
          children: [
            TextButton(
              onPressed: () => Navigator.of(context).pushNamed('/forgot'),
              child: const Text('Forgot password?'),
            ),
            TextButton(
              onPressed: () => Navigator.of(context).pushNamed('/register'),
              child: const Text('Create an account'),
            ),
          ],
        ),
      ],
    );
  }
}

class _Or extends StatelessWidget {
  const _Or();

  @override
  Widget build(BuildContext context) {
    final muted = Colors.white.withValues(alpha: .5);
    return Row(
      children: [
        const Expanded(child: Divider()),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Text('or', style: TextStyle(color: muted)),
        ),
        const Expanded(child: Divider()),
      ],
    );
  }
}

// ---- create an account -------------------------------------------------------------------

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key, required this.api});
  final Api api;

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> with Busy {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _sent = false;

  void _register() {
    if (!_form.currentState!.validate()) return;
    run(() async {
      await widget.api.register(_email.text.trim(), _password.text);
      if (mounted) setState(() => _sent = true);
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_sent) {
      return AuthFrame(
        title: 'Check your email',
        subtitle:
            'We sent a link to ${_email.text.trim()}. Open it to finish creating your '
            'account. It works for 24 hours.',
        children: const [],
      );
    }
    return AuthFrame(
      title: 'Create an account',
      children: [
        Form(
          key: _form,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextFormField(
                controller: _email,
                decoration: const InputDecoration(labelText: 'Email'),
                keyboardType: TextInputType.emailAddress,
                autofillHints: const [AutofillHints.email],
                validator: _emailError,
              ),
              _gap,
              TextFormField(
                controller: _password,
                decoration: const InputDecoration(
                  labelText: 'Password',
                  helperText: 'At least 10 characters',
                ),
                obscureText: true,
                autofillHints: const [AutofillHints.newPassword],
                validator: _passwordError,
                onFieldSubmitted: (_) => _register(),
              ),
              _gap,
              errorText(),
              submit('Create account', _register),
            ],
          ),
        ),
        const SizedBox(height: 16),
        TextButton(
          onPressed: () => Navigator.of(context).pushNamed('/login'),
          child: const Text('I already have an account'),
        ),
      ],
    );
  }
}

// ---- confirm (from the email link) -------------------------------------------------------

class ConfirmScreen extends StatefulWidget {
  const ConfirmScreen({super.key, required this.api, required this.token});
  final Api api;
  final String token;

  @override
  State<ConfirmScreen> createState() => _ConfirmScreenState();
}

class _ConfirmScreenState extends State<ConfirmScreen> with Busy {
  final _password = TextEditingController();

  // Confirming signs you in, so go straight to the dashboard.
  void _confirm() => run(() async {
    await widget.api.confirm(widget.token, _password.text);
    if (mounted) _goHome(context);
  });

  @override
  Widget build(BuildContext context) {
    return AuthFrame(
      title: 'Confirm your account',
      subtitle: 'Enter the password you chose when you signed up.',
      children: [
        TextField(
          controller: _password,
          decoration: const InputDecoration(labelText: 'Password'),
          obscureText: true,
          autofillHints: const [AutofillHints.password],
          onSubmitted: (_) => _confirm(),
        ),
        _gap,
        errorText(),
        submit('Confirm', _confirm),
      ],
    );
  }
}

// ---- finish Google sign-in (from the email link) -----------------------------------------

class GoogleConfirmScreen extends StatefulWidget {
  const GoogleConfirmScreen({super.key, required this.api, required this.token});
  final Api api;
  final String token;

  @override
  State<GoogleConfirmScreen> createState() => _GoogleConfirmScreenState();
}

class _GoogleConfirmScreenState extends State<GoogleConfirmScreen> with Busy {
  String? _googleClientId;

  @override
  void initState() {
    super.initState();
    widget.api.googleClientId().then((id) {
      if (mounted) setState(() => _googleClientId = id);
    }, onError: (_) {});
  }

  void _google(String idToken) => run(() async {
    await widget.api.googleConfirm(widget.token, idToken);
    if (mounted) _goHome(context);
  });

  @override
  Widget build(BuildContext context) {
    return AuthFrame(
      title: 'Finish signing in with Google',
      subtitle: 'Continue with the same Google account you used to sign in.',
      children: [
        if (_googleClientId != null)
          Center(
            child: GoogleButton(clientId: _googleClientId!, onIdToken: _google),
          ),
        _gap,
        errorText(),
      ],
    );
  }
}

// ---- forgot and reset password -----------------------------------------------------------

class ForgotScreen extends StatefulWidget {
  const ForgotScreen({super.key, required this.api});
  final Api api;

  @override
  State<ForgotScreen> createState() => _ForgotScreenState();
}

class _ForgotScreenState extends State<ForgotScreen> with Busy {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  bool _sent = false;

  void _send() {
    if (!_form.currentState!.validate()) return;
    run(() async {
      await widget.api.forgotPassword(_email.text.trim());
      if (mounted) setState(() => _sent = true);
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_sent) {
      return const AuthFrame(
        title: 'Check your email',
        subtitle:
            'If there\'s an account for that address, we sent it a link to choose a new '
            'password. It works for an hour.',
        children: [],
      );
    }
    return AuthFrame(
      title: 'Forgot your password?',
      subtitle: 'We\'ll email you a link to choose a new one.',
      children: [
        Form(
          key: _form,
          child: TextFormField(
            controller: _email,
            decoration: const InputDecoration(labelText: 'Email'),
            keyboardType: TextInputType.emailAddress,
            autofillHints: const [AutofillHints.email],
            validator: _emailError,
            onFieldSubmitted: (_) => _send(),
          ),
        ),
        _gap,
        errorText(),
        submit('Send link', _send),
      ],
    );
  }
}

class ResetScreen extends StatefulWidget {
  const ResetScreen({super.key, required this.api, required this.token});
  final Api api;
  final String token;

  @override
  State<ResetScreen> createState() => _ResetScreenState();
}

class _ResetScreenState extends State<ResetScreen> with Busy {
  final _form = GlobalKey<FormState>();
  final _password = TextEditingController();
  bool _done = false;

  void _reset() {
    if (!_form.currentState!.validate()) return;
    run(() async {
      await widget.api.resetPassword(widget.token, _password.text);
      if (mounted) setState(() => _done = true);
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_done) {
      return AuthFrame(
        title: 'Password changed',
        subtitle: 'You\'ve been signed out everywhere. Sign in with your new password.',
        children: [
          FilledButton(
            onPressed: () => Navigator.of(context).pushNamed('/login'),
            child: const Text('Sign in'),
          ),
        ],
      );
    }
    return AuthFrame(
      title: 'Choose a new password',
      children: [
        Form(
          key: _form,
          child: TextFormField(
            controller: _password,
            decoration: const InputDecoration(
              labelText: 'New password',
              helperText: 'At least 10 characters',
            ),
            obscureText: true,
            autofillHints: const [AutofillHints.newPassword],
            validator: _passwordError,
            onFieldSubmitted: (_) => _reset(),
          ),
        ),
        _gap,
        errorText(),
        submit('Save password', _reset),
      ],
    );
  }
}

void showError(BuildContext context, Object e) =>
    showMessage(context, e is ApiError ? e.message : 'Something went wrong. Please try again.');
