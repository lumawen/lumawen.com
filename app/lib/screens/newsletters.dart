// Newsletters tab: write, test, send or schedule emails to everyone subscribed.
// Contributors write and test drafts; admins also schedule, send, delete and restore.
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import 'account.dart' show showError;
import 'home.dart';

class NewslettersTab extends StatefulWidget {
  const NewslettersTab({super.key, required this.api, required this.me});
  final Api api;
  final User me;

  @override
  State<NewslettersTab> createState() => _NewslettersTabState();
}

class _NewslettersTabState extends State<NewslettersTab> with AutomaticKeepAliveClientMixin {
  List<Newsletter>? _items;
  bool _archived = false;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final items = await widget.api.newsletters(archived: _archived);
      if (mounted) setState(() => _items = items);
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _open([Newsletter? n]) async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => NewsletterEditor(api: widget.api, me: widget.me, newsletter: n),
      ),
    );
    _load();
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final items = _items;
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
                Row(
                  children: [
                    Expanded(
                      child: Text('Newsletters', style: Theme.of(context).textTheme.titleLarge),
                    ),
                    FilledButton.icon(
                      onPressed: _open,
                      icon: const Icon(Icons.edit),
                      label: const Text('Write'),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  children: [
                    for (final archived in [false, true])
                      ChoiceChip(
                        label: Text(archived ? 'Archived' : 'Current'),
                        selected: _archived == archived,
                        onSelected: (_) {
                          setState(() {
                            _archived = archived;
                            _items = null;
                          });
                          _load();
                        },
                      ),
                  ],
                ),
                const SizedBox(height: 8),
                if (items == null)
                  const Padding(
                    padding: EdgeInsets.all(32),
                    child: Center(child: CircularProgressIndicator()),
                  )
                else if (items.isEmpty)
                  Padding(
                    padding: const EdgeInsets.all(32),
                    child: Text(
                      _archived
                          ? 'Nothing archived. Deleted sent newsletters wait here for 30 days.'
                          : 'Nothing written yet. Your first newsletter starts with Write.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: muted),
                    ),
                  ),
                for (final n in items ?? const <Newsletter>[])
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(n.subject, maxLines: 1, overflow: TextOverflow.ellipsis),
                    subtitle: Text(statusLine(n), style: TextStyle(color: muted)),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => _open(n),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

String statusLine(Newsletter n) => n.deleteAfter != null
    ? 'Archived · deleted for good ${shortDate(n.deleteAfter!)}'
    : switch (n.status) {
        'scheduled' => 'Scheduled for ${shortDate(n.sendAt!)}',
        'sent' =>
          'Sent ${shortDate(n.queuedAt!)} to ${n.recipients} · '
              '${n.sent} delivered${n.pending > 0 ? ', ${n.pending} sending' : ''}'
              '${n.failed > 0 ? ', ${n.failed} failed' : ''}',
        _ => 'Draft · edited ${shortDate(n.updatedAt)}',
      };

class NewsletterEditor extends StatefulWidget {
  const NewsletterEditor({super.key, required this.api, required this.me, this.newsletter});
  final Api api;
  final User me;
  final Newsletter? newsletter;

  @override
  State<NewsletterEditor> createState() => _NewsletterEditorState();
}

class _NewsletterEditorState extends State<NewsletterEditor> {
  late Newsletter? _n = widget.newsletter;
  late final _subject = TextEditingController(text: _n?.subject);
  late final _body = TextEditingController(text: _n?.bodyText);
  bool _busy = false;

  bool get _admin => widget.me.isAdmin;

  /// Contributors can only change drafts; admins anything not yet sent.
  bool get _editable => _n == null || _n!.status == 'draft' || (_admin && _n!.status != 'sent');

  /// Unsaved changes: the fields differ from what the server has (it trims the subject).
  bool get _dirty {
    final n = _n;
    if (n == null) return _subject.text.trim().isNotEmpty || _body.text.trim().isNotEmpty;
    return _subject.text.trim() != n.subject || _body.text != n.bodyText;
  }

  @override
  void dispose() {
    _subject.dispose();
    _body.dispose();
    super.dispose();
  }

  Future<T?> _run<T>(Future<T> Function() action, {String? done}) async {
    setState(() => _busy = true);
    try {
      final result = await action();
      if (mounted && done != null) showMessage(context, done);
      return result;
    } catch (e) {
      if (mounted) showError(context, e);
      return null;
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Saves edits (creating the draft first if needed). False if something was missing.
  Future<bool> _save({bool quiet = false}) async {
    if (_subject.text.trim().isEmpty || _body.text.trim().isEmpty) {
      showMessage(context, 'Write a subject and a message first.');
      return false;
    }
    if (_n != null && !_dirty) return true;
    final saved = await _run(
      () => _n == null
          ? widget.api.createNewsletter(_subject.text, _body.text)
          : widget.api.updateNewsletter(_n!.id, _subject.text, _body.text),
      done: quiet ? null : 'Saved',
    );
    if (saved == null || !mounted) return false;
    setState(() => _n = saved);
    return true;
  }

  Future<void> _test() async {
    if (!await _save(quiet: true)) return;
    await _run(
      () => widget.api.sendTest(_n!.id),
      done: 'Test sent to you. It arrives within a minute.',
    );
  }

  Future<void> _sendNow() async {
    if (!await _save(quiet: true)) return;
    final page = await _run(() => widget.api.subscribers(status: 'subscribed'));
    if (page == null || !mounted) return;
    final count = page.counts['subscribed'] ?? 0;
    final ok = await _confirm(
      'Send to $count ${count == 1 ? 'person' : 'people'}?',
      'Everyone subscribed gets "${_subject.text.trim()}" now. This can\'t be undone.',
      'Send now',
    );
    if (!ok) return;
    final n = await _run(
      () => widget.api.schedule(_n!.id, null),
      done: 'Sending. It goes out over the next few minutes.',
    );
    if (n != null && mounted) Navigator.of(context).pop();
  }

  Future<void> _schedule() async {
    if (!await _save(quiet: true)) return;
    if (!mounted) return;
    final now = DateTime.now();
    final day = await showDatePicker(
      context: context,
      firstDate: now,
      lastDate: now.add(const Duration(days: 364)),
      initialDate: now.add(const Duration(days: 1)),
    );
    if (day == null || !mounted) return;
    final time = await showTimePicker(
      context: context,
      initialTime: const TimeOfDay(hour: 9, minute: 0),
    );
    if (time == null) return;
    final when = DateTime(day.year, day.month, day.day, time.hour, time.minute);
    if (when.isBefore(DateTime.now())) {
      if (mounted) showMessage(context, 'Pick a time in the future.');
      return;
    }
    final n = await _run(
      () => widget.api.schedule(_n!.id, when),
      done: 'Scheduled for ${shortDate(when)}',
    );
    if (n != null && mounted) setState(() => _n = n);
  }

  Future<void> _cancel() async {
    final n = await _run(() => widget.api.cancel(_n!.id), done: 'Back to draft');
    if (n != null && mounted) setState(() => _n = n);
  }

  Future<void> _delete() async {
    final n = _n;
    if (n == null) return Navigator.of(context).pop();
    final (title, text, action, done) = switch (n.status) {
      'scheduled' => (
        'Cancel and delete?',
        'It won\'t be sent, and it\'s gone for good.',
        'Delete',
        'Cancelled and deleted',
      ),
      'sent' => (
        'Archive this newsletter?',
        'It moves to Archived and is deleted for good after 30 days. You can restore it until '
            'then. It was already sent, so nobody loses their copy.',
        'Archive',
        'Archived',
      ),
      _ => ('Delete this draft?', 'It\'s gone for good.', 'Delete', 'Deleted'),
    };
    if (!await _confirm(title, text, action)) return;
    final ok = await _run(() async {
      await widget.api.deleteNewsletter(n.id);
      return true;
    }, done: done);
    if (ok == true && mounted) Navigator.of(context).pop();
  }

  Future<void> _restore() async {
    final n = await _run(() => widget.api.restore(_n!.id), done: 'Restored');
    if (n != null && mounted) setState(() => _n = n);
  }

  Future<bool> _confirm(String title, String text, String action) async =>
      await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          content: Text(text),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Not yet'),
            ),
            FilledButton(onPressed: () => Navigator.pop(context, true), child: Text(action)),
          ],
        ),
      ) ==
      true;

  @override
  Widget build(BuildContext context) {
    final n = _n;
    final muted = Colors.white.withValues(alpha: .66);
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop) return;
        final leave =
            !_dirty ||
            await _confirm(
              'Discard changes?',
              'Your edits since the last save will be lost.',
              'Discard',
            );
        if (leave && context.mounted) Navigator.of(context).pop();
      },
      child: _scaffold(n, muted),
    );
  }

  Widget _scaffold(Newsletter? n, Color muted) {
    return Scaffold(
      appBar: AppBar(
        title: Text(n == null ? 'New newsletter' : statusLabel(n.status)),
        actions: [
          if (n?.archivedAt == null && (n == null || n.status == 'draft' || _admin))
            IconButton(
              tooltip: switch (n?.status) {
                'sent' => 'Archive',
                'scheduled' => 'Cancel and delete',
                _ => 'Delete draft',
              },
              onPressed: _busy ? null : _delete,
              icon: const Icon(Icons.delete_outline),
            ),
          const SizedBox(width: 8),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 40),
        children: [
          ContentWidth(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (n != null && n.status != 'draft')
                  Padding(
                    padding: const EdgeInsets.only(bottom: 16),
                    child: Text(statusLine(n), style: TextStyle(color: muted)),
                  ),
                TextField(
                  controller: _subject,
                  enabled: _editable && n?.status != 'scheduled',
                  maxLength: 200,
                  decoration: const InputDecoration(labelText: 'Subject'),
                ),
                const SizedBox(height: 8),
                TextField(
                  controller: _body,
                  enabled: _editable && n?.status != 'scheduled',
                  minLines: 12,
                  maxLines: null,
                  keyboardType: TextInputType.multiline,
                  decoration: const InputDecoration(
                    labelText: 'Message',
                    alignLabelWithHint: true,
                    helperText:
                        'Plain text. An unsubscribe link is added at the bottom of '
                        'every email.',
                  ),
                ),
                const SizedBox(height: 20),
                if (_busy) const LinearProgressIndicator(),
                const SizedBox(height: 4),
                Wrap(
                  spacing: 10,
                  runSpacing: 10,
                  children: [
                    if (n == null || n.status == 'draft') ...[
                      OutlinedButton(
                        onPressed: _busy ? null : () => _save(),
                        child: const Text('Save'),
                      ),
                      OutlinedButton(
                        onPressed: _busy ? null : _test,
                        child: const Text('Send me a test'),
                      ),
                      if (_admin) ...[
                        OutlinedButton(
                          onPressed: _busy ? null : _schedule,
                          child: const Text('Schedule'),
                        ),
                        FilledButton(
                          onPressed: _busy ? null : _sendNow,
                          child: const Text('Send now'),
                        ),
                      ],
                    ],
                    if (n?.archivedAt != null && _admin)
                      FilledButton(
                        onPressed: _busy ? null : _restore,
                        child: const Text('Restore'),
                      ),
                    if (n?.status == 'scheduled' && _admin) ...[
                      OutlinedButton(
                        onPressed: _busy ? null : _test,
                        child: const Text('Send me a test'),
                      ),
                      OutlinedButton(
                        onPressed: _busy ? null : _cancel,
                        child: const Text('Cancel and edit'),
                      ),
                    ],
                  ],
                ),
                if (!_admin && (n == null || n.status == 'draft'))
                  Padding(
                    padding: const EdgeInsets.only(top: 16),
                    child: Text(
                      'When it\'s ready, an admin schedules or sends it.',
                      style: TextStyle(color: muted),
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

String statusLabel(String status) => switch (status) {
  'scheduled' => 'Scheduled',
  'sent' => 'Sent',
  _ => 'Draft',
};
