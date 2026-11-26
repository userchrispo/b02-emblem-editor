// Bounded undo/redo stack for the emblem editor.

export function createHistory(limit = 80) {
  const undoStack = [];
  const redoStack = [];

  return {
    push(snapshot) {
      undoStack.push(snapshot);
      if (undoStack.length > limit) undoStack.shift();
      redoStack.length = 0;
    },
    undo(currentSnapshot) {
      if (!undoStack.length) return null;
      redoStack.push(currentSnapshot);
      return undoStack.pop();
    },
    redo(currentSnapshot) {
      if (!redoStack.length) return null;
      undoStack.push(currentSnapshot);
      return redoStack.pop();
    },
    clear() {
      undoStack.length = 0;
      redoStack.length = 0;
    },
  };
}
