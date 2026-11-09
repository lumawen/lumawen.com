// Activity tab (admins and security): every staff action, newest first.
import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import 'account.dart' show showError;
import 'home.dart';

class ActivityTab extends StatefulWidget {
  const ActivityTab({super.key, required this.api});
  final Api api;

  @override
  State<ActivityTab> createState() => _ActivityTabState();
}

String describe(Activity a) => switch (a.action) {
  'user.role' =>
    'changed ${a.target} from ${roleLabels[a.detail['before']] ?? a.detail['before']} '
        'to ${roleLabels[a.detail['after']] ?? a.detail['after']}',
  'user.suspend' =>
    'suspended ${a.target}${a.detail['reason'] == null ? '' : ' (${a.detail['reason']})'}',
  'user.unsuspend' => 'unsuspended ${a.target}',
  'user.sign_out' => 'signed ${a.target} out everywhere',
  'user.delete' => 'deleted the account ${a.target}',
  'subscriber.erase' => 'erased ${a.target} from the mailing list',
  'newsletter.schedule' => 'scheduled "${a.target}"',
  'newsletter.cancel' => 'cancelled "${a.target}"',
  'newsletter.delete_draft' => 'deleted the draft "${a.target}"',
  'newsletter.delete_scheduled' => 'cancelled and deleted "${a.target}"',
  'newsletter.archive' => 'archived "${a.target}"',
  'newsletter.restore' => 'restored "${a.target}"',
  _ => '${a.action} ${a.target}',
};

class _ActivityTabState extends State<ActivityTab> with AutomaticKeepAliveClientMixin {
  final List<Activity> _entries = [];
  int _total = 0;
  String _query = '';
  Timer? _debounce;
  bool _loading = false;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  Future<void> _load({bool more = false}) async {
    setState(() => _loading = true);
    try {
      final (total, entries) = await widget.api.activity(
        q: _query,
        offset: more ? _entries.length : 0,
      );
      if (!mounted) return;
      setState(() {
        _total = total;
        if (!more) _entries.clear();
        _entries.addAll(entries);
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
                TextField(
                  decoration: const InputDecoration(
                    labelText: 'Search by who did it or to whom',
                    prefixIcon: Icon(Icons.search),
                  ),
                  onChanged: (q) {
                    _debounce?.cancel();
                    _debounce = Timer(const Duration(milliseconds: 300), () {
                      _query = q.trim();
                      _load();
                    });
                  },
                ),
                const SizedBox(height: 12),
                if (_loading && _entries.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(32),
                    child: Center(child: CircularProgressIndicator()),
                  )
                else if (_entries.isEmpty)
                  Padding(
                    padding: const EdgeInsets.all(32),
                    child: Text(
                      'Nothing yet. Role changes, suspensions, deletions and sends show up here.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: muted),
                    ),
                  ),
                for (final a in _entries)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text('${a.actor} ${describe(a)}'),
                    subtitle: Text(shortDate(a.createdAt), style: TextStyle(color: muted)),
                  ),
                if (_entries.length < _total)
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
