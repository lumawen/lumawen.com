// Users tab: everyone with a Lumawen account.
import 'package:flutter/material.dart';

import '../api.dart';
import 'account.dart' show showError;
import 'home.dart';

class UsersTab extends StatefulWidget {
  const UsersTab({super.key, required this.api});
  final Api api;

  @override
  State<UsersTab> createState() => _UsersTabState();
}

class _UsersTabState extends State<UsersTab> with AutomaticKeepAliveClientMixin {
  final List<User> _users = [];
  int _total = 0;
  bool _loading = false;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool more = false}) async {
    setState(() => _loading = true);
    try {
      final (total, users) = await widget.api.users(offset: more ? _users.length : 0);
      if (!mounted) return;
      setState(() {
        _total = total;
        if (!more) _users.clear();
        _users.addAll(users);
      });
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final muted = Colors.white.withValues(alpha: .66);
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 20, 16, 40),
        children: [
          ContentWidth(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  '$_total ${_total == 1 ? 'account' : 'accounts'}',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 8),
                if (_loading && _users.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(32),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                for (final u in _users)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(u.displayName ?? u.email),
                    subtitle: u.displayName == null
                        ? null
                        : Text(u.email, style: TextStyle(color: muted)),
                    trailing: u.isAdmin
                        ? const Chip(label: Text('Admin'), visualDensity: VisualDensity.compact)
                        : null,
                  ),
                if (_users.length < _total)
                  Center(
                    child: OutlinedButton(
                      onPressed: _loading ? null : () => _load(more: true),
                      child: const Text('Show more'),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
