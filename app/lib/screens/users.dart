// Users tab: find people by email or name, filter by role, and manage an account.
// Moderators and security suspend; security also signs people out; admins do everything.
import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import 'account.dart' show showError;
import 'home.dart';

class UsersTab extends StatefulWidget {
  const UsersTab({super.key, required this.api, required this.me});
  final Api api;
  final User me;

  @override
  State<UsersTab> createState() => _UsersTabState();
}

class _UsersTabState extends State<UsersTab> with AutomaticKeepAliveClientMixin {
  // Filter chips: null = everyone, a role, or 'suspended'.
  static const _filters = [null, ...roles, 'suspended'];

  final List<Member> _users = [];
  MemberPage? _page;
  String? _filter;
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
      final page = await widget.api.users(
        q: _query,
        role: _filter == 'suspended' ? null : _filter,
        suspended: _filter == 'suspended' ? true : null,
        offset: more ? _users.length : 0,
      );
      if (!mounted) return;
      setState(() {
        _page = page;
        if (!more) _users.clear();
        _users.addAll(page.users);
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

  Future<void> _open(Member m) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => MemberSheet(api: widget.api, me: widget.me, member: m),
    );
    _load(); // pick up whatever changed
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final muted = Colors.white.withValues(alpha: .66);
    final counts = _page?.counts ?? const {};
    final total = _page?.total ?? 0;
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
                  '$total ${total == 1 ? 'account' : 'accounts'}',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 16),
                TextField(
                  decoration: const InputDecoration(
                    labelText: 'Search by email or name',
                    prefixIcon: Icon(Icons.search),
                  ),
                  onChanged: _search,
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final f in _filters)
                      ChoiceChip(
                        label: Text(switch (f) {
                          null => 'All',
                          'suspended' => 'Suspended (${counts['suspended'] ?? 0})',
                          _ => '${roleLabels[f]} (${counts[f] ?? 0})',
                        }),
                        selected: _filter == f,
                        onSelected: (_) {
                          setState(() => _filter = f);
                          _load();
                        },
                      ),
                  ],
                ),
                const SizedBox(height: 8),
                if (_loading && _users.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(32),
                    child: Center(child: CircularProgressIndicator()),
                  )
                else if (_users.isEmpty)
                  Padding(
                    padding: const EdgeInsets.all(32),
                    child: Text(
                      'Nobody matches.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: muted),
                    ),
                  ),
                for (final u in _users)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(u.displayName ?? u.email),
                    subtitle: Text(
                      [
                        if (u.displayName != null) u.email,
                        if (u.suspendedAt != null) 'Suspended',
                        'joined ${shortDate(u.createdAt)}',
                      ].join(' · '),
                      style: TextStyle(color: muted),
                    ),
                    trailing: u.role == 'user'
                        ? const Icon(Icons.chevron_right)
                        : Chip(
                            label: Text(roleLabels[u.role] ?? u.role),
                            visualDensity: VisualDensity.compact,
                          ),
                    onTap: () => _open(u),
                  ),
                if (_page != null && _users.length < total)
                  Center(
                    child: OutlinedButton(
                      onPressed: _loading ? null : () => _load(more: true),
                      child: Text('Show more (${total - _users.length} left)'),
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

/// One person's details and what the signed-in staff member may do to them.
class MemberSheet extends StatefulWidget {
  const MemberSheet({super.key, required this.api, required this.me, required this.member});
  final Api api;
  final User me;
  final Member member;

  @override
  State<MemberSheet> createState() => _MemberSheetState();
}

class _MemberSheetState extends State<MemberSheet> {
  // Whom each role may suspend. Mirrors CAN_SUSPEND in api/src/lumawen_api/staff.py.
  static const _canSuspend = {
    'moderator': {'user', 'contributor'},
    'security': {'user', 'contributor', 'moderator'},
    'admin': {...roles},
  };

  late Member _m = widget.member;
  bool _busy = false;

  bool get _self => _m.id == widget.me.id;

  Future<void> _run(Future<Member?> Function() action, String done) async {
    setState(() => _busy = true);
    try {
      final m = await action();
      if (!mounted) return;
      showMessage(context, done);
      if (m == null) {
        Navigator.of(context).pop();
      } else {
        setState(() => _m = m);
      }
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<bool> _confirm(String title, String text, String action, {Widget? extra}) async =>
      await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [Text(text), ?extra],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Keep')),
            FilledButton(onPressed: () => Navigator.pop(context, true), child: Text(action)),
          ],
        ),
      ) ==
      true;

  Future<void> _suspend() async {
    final reason = TextEditingController();
    final ok = await _confirm(
      'Suspend ${_m.email}?',
      'They\'re signed out everywhere and can\'t sign in until unsuspended.',
      'Suspend',
      extra: Padding(
        padding: const EdgeInsets.only(top: 12),
        child: TextField(
          controller: reason,
          maxLength: 200,
          decoration: const InputDecoration(labelText: 'Reason (only staff see it)'),
        ),
      ),
    );
    if (ok) await _run(() => widget.api.suspend(_m.id, reason.text), 'Suspended');
    reason.dispose();
  }

  Future<void> _delete() async {
    final ok = await _confirm(
      'Delete ${_m.email}?',
      'The account, its sign-ins and its emails are gone for good. Mailing-list sign-ups are '
          'separate and stay.',
      'Delete',
    );
    if (ok) {
      await _run(() async {
        await widget.api.deleteUser(_m.id);
        return null;
      }, 'Deleted ${_m.email}');
    }
  }

  @override
  Widget build(BuildContext context) {
    final me = widget.me;
    final m = _m;
    final muted = Colors.white.withValues(alpha: .66);
    final canSuspend = !_self && (_canSuspend[me.role]?.contains(m.role) ?? false);
    final canSignOut = me.canSeeActivity && (m.role != 'admin' || me.isAdmin);
    String? when(DateTime? d) => d == null ? null : shortDate(d);
    final facts = <(String, String?)>[
      ('Joined', when(m.createdAt)),
      ('Last seen', when(m.lastSeenAt) ?? 'Never'),
      (
        'Signs in with',
        [if (m.hasPassword) 'password', if (m.hasGoogle) 'Google'].join(' and ').ifEmpty('—'),
      ),
      ('Signed in on', '${m.sessions} ${m.sessions == 1 ? 'device' : 'devices'}'),
      ('Email', m.emailVerified ? 'Confirmed' : 'Not confirmed yet'),
      if (m.suspendedAt != null)
        (
          'Suspended',
          '${when(m.suspendedAt)}${m.suspendedReason == null ? '' : ' · ${m.suspendedReason}'}',
        ),
    ];
    return SafeArea(
      child: SingleChildScrollView(
        padding: EdgeInsets.fromLTRB(20, 0, 20, 20 + MediaQuery.viewInsetsOf(context).bottom),
        child: ContentWidth(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(m.displayName ?? m.email, style: Theme.of(context).textTheme.titleLarge),
              if (m.displayName != null) Text(m.email, style: TextStyle(color: muted)),
              const SizedBox(height: 16),
              for (final (label, value) in facts)
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(
                        width: 120,
                        child: Text(label, style: TextStyle(color: muted)),
                      ),
                      Expanded(child: Text(value ?? '—')),
                    ],
                  ),
                ),
              const SizedBox(height: 12),
              if (me.isAdmin && !_self)
                DropdownButtonFormField<String>(
                  initialValue: m.role,
                  decoration: const InputDecoration(labelText: 'Role'),
                  items: [
                    for (final r in roles) DropdownMenuItem(value: r, child: Text(roleLabels[r]!)),
                  ],
                  onChanged: _busy
                      ? null
                      : (r) {
                          if (r != null && r != m.role) {
                            _run(() => widget.api.setRole(m.id, r), 'Now ${roleLabels[r]}');
                          }
                        },
                )
              else
                Text('Role: ${roleLabels[m.role] ?? m.role}'),
              const SizedBox(height: 16),
              if (_busy) const LinearProgressIndicator(),
              const SizedBox(height: 4),
              Wrap(
                spacing: 10,
                runSpacing: 10,
                children: [
                  if (canSuspend && m.suspendedAt == null)
                    OutlinedButton(
                      onPressed: _busy ? null : _suspend,
                      child: const Text('Suspend'),
                    ),
                  if (canSuspend && m.suspendedAt != null)
                    FilledButton(
                      onPressed: _busy
                          ? null
                          : () => _run(() => widget.api.unsuspend(m.id), 'Unsuspended'),
                      child: const Text('Unsuspend'),
                    ),
                  if (canSignOut && m.sessions > 0)
                    OutlinedButton(
                      onPressed: _busy
                          ? null
                          : () => _run(
                              () => widget.api.signOutEverywhere(m.id),
                              'Signed out everywhere',
                            ),
                      child: const Text('Sign out everywhere'),
                    ),
                  if (me.isAdmin && !_self)
                    TextButton(
                      onPressed: _busy ? null : _delete,
                      child: const Text('Delete account'),
                    ),
                ],
              ),
              if (_self)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(
                    'This is you. Another admin can change your role.',
                    style: TextStyle(color: muted),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

extension on String {
  String ifEmpty(String other) => isEmpty ? other : this;
}
