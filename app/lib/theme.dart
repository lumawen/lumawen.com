import 'package:flutter/material.dart';

/// Black and white only, Lumawen Sans, like lumawen.com.
ThemeData lumawenTheme() {
  const white = Colors.white;
  const black = Colors.black;
  final muted = white.withValues(alpha: .66);
  final hair = white.withValues(alpha: .22);
  const scheme = ColorScheme.dark(
    primary: white,
    onPrimary: black,
    secondary: white,
    onSecondary: black,
    surface: black,
    onSurface: white,
    error: Color(0xFFFF8A80),
    onError: black,
  );
  final pill = RoundedRectangleBorder(borderRadius: BorderRadius.circular(999));
  const pad = EdgeInsets.symmetric(horizontal: 22, vertical: 16);
  const label = TextStyle(fontFamily: 'Lumawen Sans', fontWeight: FontWeight.w700, fontSize: 15);
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: black,
    fontFamily: 'Lumawen Sans',
    dividerColor: white.withValues(alpha: .14),
    appBarTheme: const AppBarTheme(backgroundColor: black, surfaceTintColor: black),
    textTheme: const TextTheme(
      headlineMedium: TextStyle(fontWeight: FontWeight.w700, letterSpacing: -0.8),
      titleLarge: TextStyle(fontWeight: FontWeight.w700, letterSpacing: -0.3),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: white,
        foregroundColor: black,
        shape: pill,
        padding: pad,
        textStyle: label,
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: white,
        side: BorderSide(color: hair, width: 1.5),
        shape: pill,
        padding: pad,
        textStyle: label,
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(foregroundColor: white, textStyle: label),
    ),
    inputDecorationTheme: InputDecorationTheme(
      labelStyle: TextStyle(color: muted),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: hair),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: white, width: 1.5),
      ),
    ),
    chipTheme: ChipThemeData(
      shape: pill,
      side: BorderSide(color: hair),
      selectedColor: white,
      checkmarkColor: black,
      secondaryLabelStyle: const TextStyle(color: black),
    ),
    snackBarTheme: const SnackBarThemeData(
      backgroundColor: white,
      contentTextStyle: TextStyle(color: black),
    ),
    tabBarTheme: TabBarThemeData(
      labelColor: white,
      unselectedLabelColor: muted,
      indicatorColor: white,
      dividerColor: white.withValues(alpha: .14),
      labelStyle: const TextStyle(fontFamily: 'Lumawen Sans', fontWeight: FontWeight.w700),
      unselectedLabelStyle: const TextStyle(fontFamily: 'Lumawen Sans'),
    ),
  );
}

/// The flame pick mark.
class Mark extends StatelessWidget {
  const Mark({super.key, this.height = 36});
  final double height;

  @override
  Widget build(BuildContext context) =>
      Image.asset('assets/img/mark-white.png', height: height, semanticLabel: 'Lumawen');
}

void showMessage(BuildContext context, String text) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(text)));
}
