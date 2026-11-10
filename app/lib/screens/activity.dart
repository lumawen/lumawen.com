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
  'user.delete' => 'deleted the account ${_erased(a)} from users${_since(a, 'role', 'joined')}',
  'subscriber.erase' =>
    'deleted the subscriber ${_erased(a)} from the mailing list${_since(a, 'status', 'signed_up')}',
  'newsletter.edit_draft' => 'edited the draft "${a.target}"',
  'newsletter.edit_scheduled' => 'edited the scheduled "${a.target}"',
  'newsletter.schedule' => 'scheduled "${a.target}"',
  'newsletter.cancel' => 'cancelled "${a.target}"',
  'newsletter.delete_draft' => 'deleted the draft "${a.target}"',
  'newsletter.delete_scheduled' => 'cancelled and deleted "${a.target}"',
  'newsletter.archive' => 'archived "${a.target}"',
  'newsletter.restore' => 'restored "${a.target}"',
  _ => '${a.action} ${a.target}',
};

// The address is blanked 30 days after a delete (rows before 2026-11-10 never had one).
String _erased(Activity a) => a.target.startsWith('(') ? '(address erased)' : "'${a.target}'";

// " (Contributor, joined 2026-11-09)" from the row's details, when it has them.
String _since(Activity a, String kindKey, String dateKey) {
  final kind = a.detail[kindKey] as String?;
  final date = a.detail[dateKey] as String?;
  final verb = dateKey == 'joined' ? 'joined' : 'signed up';
  final parts = [if (kind != null) roleLabels[kind] ?? kind, if (date != null) '$verb $date'];
  return parts.isEmpty ? '' : ' (${parts.join(', ')})';
}

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

  int _request = 0; // only the newest search's answer is shown

  Future<void> _load({bool more = false}) async {
    final request = ++_request;
    setState(() => _loading = true);
    try {
      final (total, entries) = await widget.api.activity(
        q: _query,
        offset: more ? _entries.length : 0,
      );
      if (!mounted || request != _request) return;
      setState(() {
        _total = total;
        if (!more) _entries.clear();
        // New actions push older ones down a page, so skip any already shown.
        final shown = {for (final e in _entries) e.id};
        _entries.addAll(entries.where((e) => !shown.contains(e.id)));
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
