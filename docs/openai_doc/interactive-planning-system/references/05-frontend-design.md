# Frontend visual and interaction design
Layout: top shared Ask/Plan/Agent segmented switch; center conversation stream; optional collapsible right rail for goal, decisions, plan progress; fixed composer.
Assistant message: short framing paragraph, then compact decision card; avoid dense full-screen forms. Card max width ~640px, consistent radius 12-16px, restrained borders, no heavy shadows.
Single select: selectable rows with radio indicator, title, one-line description; persistent selected state; explicit submit when consequential. Multi-select: checkboxes and selection count. Confirmation: clear consequences and separate cancel. Plan card: step list, dependencies, revise/approve controls. Progress card: status + expandable detail.
Interaction states: idle, selected, submitting, submitted, error, stale, skipped, disabled. Distinguish selected from committed. Optimistic UI must reconcile with server result.
Responsive: mobile full-width cards and collapsible rail. Keyboard navigation, visible focus, aria labels, sufficient contrast, reduced motion.
Streaming: text may appear incrementally, but cards mount only after a fully validated interaction.required event; no half-rendered schema.
Modes share message list and conversation id. Switching mode does not automatically approve a plan or start Agent.
