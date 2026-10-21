# Browser acceptance screenshots

Captured from the actual local BP-Reviewer UI in Edge on 2026-10-21 using only `examples/tiny_pipeline`. These are browser captures, not design mockups. The password field is masked; project paths are relative. The annotation toolbar belongs to the browser extension and is not part of BP-Reviewer.

- [proposed-rewire.jpg](proposed-rewire.jpg): the load output is connected to summary input B in Proposed; input A is disconnected and the proposal is UNVERIFIED.
- [rewire-diff.jpg](rewire-diff.jpg): two semantic changes, removing the declared A connection and adding the unverified B connection.
- [source-evidence.jpg](source-evidence.jpg): module expansion through the level selector, function selection and the actual Python source excerpt with STATIC_INFERRED evidence.

Actual pointer drag, reconnect and disconnect passed. Undo produced zero semantic changes; redo restored two. The saved revision 1 survived a full page reload and rescan. As-is retained the original A connection with editing disabled. JSON and Markdown downloads matched the displayed Change Request. The three example files retained their original SHA256 hashes. Chinese port explanations and zoom controls were checked in the UI.

Module expansion was accepted through the level selector. Automated double-click attempts did not establish a stable result and are not counted as a separate passed interaction. These captures do not claim runtime execution evidence or complete data-flow discovery.

Machine-specific exports and acceptance details remain in ignored `state/browser-validation/` and are not committed.
