// After sign-in: admins get the Newsletters, Mailing list and Users tabs.
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import 'mailing_list.dart';
import 'newsletters.dart';
import 'users.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.api});
  final Api api;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  User? _user;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final user = await widget.api.me();
      if (!mounted) return;
      if (user == null) {
        Navigator.of(context).pushReplacementNamed('/login');
      } else {
        setState(() => _user = user);
      }
    } catch (_) {
      if (mounted) setState(() => _error = 'Can\'t reach Lumawen right now.');
    }
  }

  Future<void> _logout() async {
    try {
      await widget.api.logout();
    } catch (_) {}
    if (mounted) Navigator.of(context).pushNamedAndRemoveUntil('/login', (_) => false);
  }

  @override
  Widget build(BuildContext context) {
    final user = _user;
    if (user == null) {
      return Scaffold(
        body: Center(
          child: _error == null
              ? const CircularProgressIndicator()
              : Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(_error!),
                    const SizedBox(height: 16),
                    OutlinedButton(
                      onPressed: () {
                        setState(() => _error = null);
                        _load();
                      },
                      child: const Text('Try again'),
                    ),
                  ],
                ),
        ),
      );
    }
    final bar = AppBar(
      automaticallyImplyLeading: false,
      toolbarHeight: 68,
      titleSpacing: 16,
      title: const Row(
        children: [
          Mark(height: 32),
          SizedBox(width: 12),
          Text('Dashboard', style: TextStyle(fontWeight: FontWeight.w700)),
        ],
      ),
      actions: [
        PopupMenuButton<String>(
          tooltip: 'Account',
          icon: const Icon(Icons.account_circle_outlined),
          onSelected: (_) => _logout(),
          itemBuilder: (_) => [
            PopupMenuItem(enabled: false, child: Text(user.email)),
            const PopupMenuItem(value: 'logout', child: Text('Sign out')),
          ],
        ),
        const SizedBox(width: 8),
      ],
    );
    if (!user.isAdmin) {
      return Scaffold(
        appBar: bar,
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Text(
              'You\'re signed in as ${user.email}. More is coming here soon.',
              textAlign: TextAlign.center,
            ),
          ),
        ),
      );
    }
    return DefaultTabController(
      length: 3,
      child: Scaffold(
        appBar: AppBar(
          automaticallyImplyLeading: false,
          toolbarHeight: bar.toolbarHeight,
          titleSpacing: bar.titleSpacing,
          title: bar.title,
          actions: bar.actions,
          bottom: const TabBar(
            isScrollable: true,
            tabAlignment: TabAlignment.start,
            tabs: [
              Tab(text: 'Newsletters'),
              Tab(text: 'Mailing list'),
              Tab(text: 'Users'),
            ],
          ),
        ),
        body: TabBarView(
          children: [
            NewslettersTab(api: widget.api),
            MailingListTab(api: widget.api),
            UsersTab(api: widget.api),
          ],
        ),
      ),
    );
  }
}

/// Keeps tab content readable on wide screens.
class ContentWidth extends StatelessWidget {
  const ContentWidth({super.key, required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) => Align(
    alignment: Alignment.topCenter,
    child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 880), child: child),
  );
}

String shortDate(DateTime d) {
  const months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  final h = d.hour % 12 == 0 ? 12 : d.hour % 12;
  final m = d.minute.toString().padLeft(2, '0');
  return '${months[d.month - 1]} ${d.day}, ${d.year}, $h:$m ${d.hour < 12 ? 'am' : 'pm'}';
}
