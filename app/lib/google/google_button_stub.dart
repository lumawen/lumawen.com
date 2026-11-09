import 'package:flutter/widgets.dart';

/// The Android app gets its own Google button with the app step.
class GoogleButton extends StatelessWidget {
  const GoogleButton({super.key, required this.clientId, required this.onIdToken});
  final String clientId;
  final ValueChanged<String> onIdToken;

  @override
  Widget build(BuildContext context) => const SizedBox.shrink();
}
