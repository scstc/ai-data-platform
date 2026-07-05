import {
  DndContext,
  type DragEndEvent,
  DragOverlay,
  type DragStartEvent,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import { useState } from 'react';

/** 编辑器三栏外层拖拽容器:统一承接两类拖拽——
 *  1) 算子库条目(id 前缀 `op:`,见 OperatorLibrary)拖入流水线区 → 视为 append,
 *     不区分具体落点(拖到已有步骤上或空白处均追加到末尾);
 *  2) 流水线内部已选步骤(id 为 PipelineSteps 的 `${name}-${idx}`)互拖 → 排序。
 *  PipelineSteps 自身只负责 useDroppable + SortableContext,不再持有 DndContext。 */
const PipelineDndArea: React.FC<{
  steps: DataPlatform.PipelineStep[];
  labelOf: (name: string) => string;
  onAppend: (name: string) => void;
  onReorder: (from: number, to: number) => void;
  children: React.ReactNode;
}> = ({ steps, labelOf, onAppend, onReorder, children }) => {
  const sensors = useSensors(useSensor(PointerSensor));
  const [activeLabel, setActiveLabel] = useState<string>();
  const stepIds = steps.map((s, i) => `${s.name}-${i}`);

  const onDragStart = (e: DragStartEvent) => {
    const id = String(e.active.id);
    if (id.startsWith('op:')) {
      setActiveLabel(labelOf(id.slice(3)));
      return;
    }
    const idx = stepIds.indexOf(id);
    setActiveLabel(idx >= 0 ? labelOf(steps[idx].name) : undefined);
  };

  const onDragEnd = (e: DragEndEvent) => {
    setActiveLabel(undefined);
    const { active, over } = e;
    if (!over) return;
    const activeId = String(active.id);
    if (activeId.startsWith('op:')) {
      onAppend(activeId.slice(3));
      return;
    }
    if (active.id === over.id) return;
    const from = stepIds.indexOf(activeId);
    const to = stepIds.indexOf(String(over.id));
    if (from >= 0 && to >= 0) onReorder(from, to);
  };

  return (
    <DndContext
      sensors={sensors}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={() => setActiveLabel(undefined)}
    >
      {children}
      <DragOverlay>
        {activeLabel ? (
          <div
            style={{
              padding: '6px 12px',
              borderRadius: 6,
              background: '#fff',
              border: '1px solid #1677ff',
              boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
              fontSize: 13,
            }}
          >
            {activeLabel}
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
};

export default PipelineDndArea;
