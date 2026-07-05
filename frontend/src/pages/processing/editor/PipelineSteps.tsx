import { DeleteOutlined, HolderOutlined } from '@ant-design/icons';
import { useDroppable } from '@dnd-kit/core';
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Button, Empty, Typography } from 'antd';

const { Text } = Typography;

const Row: React.FC<{
  id: string;
  index: number;
  label: string;
  active: boolean;
  onSelect: () => void;
  onRemove: () => void;
}> = ({ id, index, label, active, onSelect, onRemove }) => {
  const { attributes, listeners, setNodeRef, transform, transition } =
    useSortable({ id });
  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '8px 12px',
        marginBottom: 8,
        borderRadius: 6,
        border: active ? '1px solid #1677ff' : '1px solid #f0f0f0',
        background: active ? '#e6f4ff' : '#fff',
        cursor: 'pointer',
      }}
      onClick={onSelect}
    >
      <span
        {...attributes}
        {...listeners}
        style={{ cursor: 'grab', color: '#999' }}
      >
        <HolderOutlined />
      </span>
      <Text style={{ flex: 1 }}>
        {index + 1}. {label}
      </Text>
      <Button
        type="text"
        size="small"
        danger
        icon={<DeleteOutlined />}
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
      />
    </div>
  );
};

/** 中栏:有序步骤列表,点选高亮。容器本身是拖放目标(算子库条目拖入即 append,
 *  由外层 PipelineDndArea 统一处理 DndContext/onDragEnd);已选步骤间排序沿用
 *  SortableContext。 */
const PipelineSteps: React.FC<{
  steps: DataPlatform.PipelineStep[];
  labelOf: (name: string) => string;
  activeIdx: number;
  onSelect: (idx: number) => void;
  onRemove: (idx: number) => void;
}> = ({ steps, labelOf, activeIdx, onSelect, onRemove }) => {
  const { setNodeRef, isOver } = useDroppable({ id: 'pipeline-dropzone' });
  const ids = steps.map((s, i) => `${s.name}-${i}`);
  return (
    <div
      ref={setNodeRef}
      style={{
        minHeight: '100%',
        borderRadius: 6,
        outline: isOver ? '2px dashed #1677ff' : 'none',
      }}
    >
      {!steps.length ? (
        <Empty
          description="从左侧算子库添加或拖入算子,组成处理流水线"
          style={{ padding: '48px 0' }}
        />
      ) : (
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          {steps.map((s, i) => (
            <Row
              key={ids[i]}
              id={ids[i]}
              index={i}
              label={labelOf(s.name)}
              active={i === activeIdx}
              onSelect={() => onSelect(i)}
              onRemove={() => onRemove(i)}
            />
          ))}
        </SortableContext>
      )}
    </div>
  );
};

export default PipelineSteps;
