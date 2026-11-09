// Mailing list tab: counts, filter, search, export and erase subscribers.
import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../platform/open_url.dart';
import '../theme.dart';
import 'account.dart' show showError;
import 'home.dart';

class MailingListTab extends StatefulWidget {
  const MailingListTab({super.key, required this.api});
  final Api api;

  @override
  State<MailingListTab> createState() => _MailingListTabState();
}

class _MailingListTabState extends State<MailingListTab> with AutomaticKeepAliveClientMixin {
  static const _filters = {
    null: 'All',
    'subscribed': 'Subscribed',
    'pending': 'Not confirmed',
    'unsubscribed': 'Unsubscribed',
  };

  String? _status;
  String _query = '';
  Timer? _debounce;
  SubscriberPage? _page;
  final List<Subscriber> _rows = [];
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
      final page = await widget.api.subscribers(
        status: _status,
        q: _query,
        offset: more ? _rows.length : 0,
      );
      if (!mounted) return;
      setState(() {
        _page = page;
        if (!more) _rows.clear();
        _rows.addAll(page.subscribers);
      });
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _search(String q) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 300), () {
      _query = q.trim();
      _load();
    });
  }

  Future<void> _erase(Subscriber s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Erase subscriber?'),
        content: Text(
          'This removes ${s.email} and every email to them for good. To just stop '
          'emails, they can unsubscribe from any newsletter.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Keep')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Erase')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.api.deleteSubscriber(s.id);
      if (!mounted) return;
      showMessage(context, 'Erased ${s.email}');
      _load();
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final counts = _page?.counts ?? const {};
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
                Wrap(
                  spacing: 28,
                  runSpacing: 12,
                  crossAxisAlignment: WrapCrossAlignment.end,
                  children: [
                    _Stat(label: 'Subscribed', value: counts['subscribed']),
                    _Stat(label: 'Not confirmed', value: counts['pending']),
                    _Stat(label: 'Unsubscribed', value: counts['unsubscribed']),
                    OutlinedButton.icon(
                      onPressed: () => openUrl(widget.api.subscribersCsv()),
                      icon: const Icon(Icons.download, size: 18),
                      label: const Text('Export CSV'),
                    ),
                  ],
                ),
                const SizedBox(height: 24),
                TextField(
                  decoration: const InputDecoration(
                    labelText: 'Search by email',
                    prefixIcon: Icon(Icons.search),
                  ),
                  onChanged: _search,
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final entry in _filters.entries)
                      ChoiceChip(
                        label: Text(entry.value),
                        selected: _status == entry.key,
                        onSelected: (_) {
                          setState(() => _status = entry.key);
                          _load();
                        },
                      ),
                  ],
                ),
                const SizedBox(height: 12),
                if (_loading && _rows.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(32),
                    child: Center(child: CircularProgressIndicator()),
                  )
                else if (_rows.isEmpty)
                  Padding(
                    padding: const EdgeInsets.all(32),
                    child: Text(
                      'Nobody here yet.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: muted),
                    ),
                  ),
                for (final s in _rows)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(s.email),
                    subtitle: Text(
                      '${_filters[s.status]} · since ${shortDate(s.createdAt)}',
                      style: TextStyle(color: muted),
                    ),
                    trailing: IconButton(
                      tooltip: 'Erase',
                      icon: const Icon(Icons.delete_outline),
                      onPressed: () => _erase(s),
                    ),
                  ),
                if (_page != null && _rows.length < _page!.total)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Center(
                      child: OutlinedButton(
                        onPressed: _loading ? null : () => _load(more: true),
                        child: Text('Show more (${_page!.total - _rows.length} left)'),
                      ),
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

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value});
  final String label;
  final int? value;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    mainAxisSize: MainAxisSize.min,
    children: [
      Text(
        value?.toString() ?? '–',
        style: const TextStyle(fontSize: 32, fontWeight: FontWeight.w700, height: 1.1),
      ),
      Text(label, style: TextStyle(color: Colors.white.withValues(alpha: .66))),
    ],
  );
}
