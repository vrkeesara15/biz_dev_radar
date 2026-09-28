/**
 * The pursuit panels M6-09 owns, re-exported so the M5-18 pursuit workspace
 * can embed them in its Tasks and Activity tabs without reaching into file
 * paths: `import { KeyDatesPanel, TasksPanel, CommentsThread } from
 * "@/components/pursuits"`.
 */
export { KeyDatesPanel, type KeyDatesPanelProps } from "./key-dates-panel";
export { TasksPanel, type TasksPanelProps } from "./tasks-panel";
export { CommentsThread, type CommentsThreadProps } from "./comments-thread";
export { PursuitHeader, type PursuitHeaderProps } from "./pursuit-header";
