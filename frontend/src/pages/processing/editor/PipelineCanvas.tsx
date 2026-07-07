// 算子流水线画布:xyflow 自由画布 + 强制线性链(dj-process 引擎只认线性算子序列)。
// 「输入」→ N 个算子节点 →「输出」固定首尾且不可删;中间算子节点靠边首尾相连,
// 边校验保证每节点最多一进一出、禁自环成环。调整顺序两种方式:拖动主链节点松手后
// 按 x 坐标重排主链连线(onNodeDragStop);或删边重连手工改链。
//
// steps(父组件受控数组)是唯一数据源。父组件可能在每次渲染时重建 steps 数组
// (如 processing/editor 的 memberSteps 由 cfg.operators.map 现算),因此同步
// **只看算子名序列**(prevNamesRef)而非引用:名序相同 → 拓扑不动(参数编辑/无关
// 重渲);追加一个 → 接主链尾;删掉一个 → 摘节点桥接前后边;其余(切成员 Tab、
// pipelineId 预载)→ 整体重建为默认横向链。节点 id(step-N 单调递增)与 steps
// 下标的对应关系维护在 stepNodeIdsRef,拓扑不变时映射稳定,用户拖拽位置不丢。
//
// 防回环(onOrderChange → 父 setState → steps 回流 → 再触发)三道闸:
// 1. emitOrder 只在用户边操作(onConnect / onEdgesChange 删边)与节点删除后调用,
//    渲染期与名序相同的同步路径绝不上报;
// 2. 恒等排列不上报(唯一例外:游离刚被接回主链时补报一次,让父组件解除阻断),
//    游离状态只在变化沿上报(orphanRef 去重);
// 3. 上报非恒等全量排列时记 pendingPermRef,父组件重排后 steps 回流命中该排列
//    → 只同步 id 映射,不再上报。
import { DeleteOutlined, NodeIndexOutlined } from '@ant-design/icons';
import { useDroppable } from '@dnd-kit/core';
import {
  addEdge,
  applyEdgeChanges,
  Background,
  type Connection,
  Controls,
  type Edge,
  type EdgeChange,
  Handle,
  MarkerType,
  type Node,
  type NodeProps,
  Panel,
  Position,
  ReactFlow,
  type ReactFlowInstance,
  useNodesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Button, message, Typography } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';

const { Text } = Typography;

const STEP_GAP = 220;
const LINEAR_WARN = '线性流水线：每个算子只能一进一出';

/** DJ 原生类别 → 色标(口径对齐 processing/market/_labels.ts 的 CATEGORY_LABEL) */
const CATEGORY_COLOR: Record<string, string> = {
  mapper: '#1677ff',
  filter: '#fa8c16',
  deduplicator: '#722ed1',
};
const DEFAULT_COLOR = '#8c8c8c';

const mkEdge = (source: string, target: string): Edge => ({
  id: `${source}->${target}`,
  source,
  target,
  type: 'smoothstep',
  markerEnd: { type: MarkerType.ArrowClosed },
});

/** 沿出边从 input 走到 output,返回途经的算子节点 id 序列;中途断链/到不了 output
 *  视为空(此时全部算子节点都不算在主链上)。 */
const computeChain = (edges: Edge[]): string[] => {
  const bySource = new Map(edges.map((e) => [e.source, e.target]));
  const order: string[] = [];
  const seen = new Set<string>(['input']);
  let cur = bySource.get('input');
  while (cur !== undefined) {
    if (cur === 'output') return order;
    if (seen.has(cur)) return [];
    seen.add(cur);
    order.push(cur);
    cur = bySource.get(cur);
  }
  return [];
};

/** source→target 是否会在现有边基础上闭环:从 target 沿出边一路追,若走回 source 即成环 */
const wouldCycle = (source: string, target: string, edges: Edge[]): boolean => {
  const bySource = new Map(edges.map((e) => [e.source, e.target]));
  let cur: string | undefined = target;
  const seen = new Set<string>();
  while (cur !== undefined) {
    if (cur === source) return true;
    if (seen.has(cur)) return false;
    seen.add(cur);
    cur = bySource.get(cur);
  }
  return false;
};

type EndpointData = { label: string };
/** 输入/输出固定端点:输入只出不进,输出只进不出;不可删(见节点创建时 deletable:false)。 */
const EndpointNode = ({ data, id }: NodeProps) => {
  const d = data as unknown as EndpointData;
  const isInput = id === 'input';
  return (
    <div
      style={{
        width: 160,
        padding: '10px 12px',
        borderRadius: 8,
        background: 'var(--ant-color-fill-quaternary)',
        border: '1px dashed var(--ant-color-border)',
        textAlign: 'center',
      }}
    >
      {!isInput && <Handle type="target" position={Position.Left} />}
      <Text ellipsis style={{ fontSize: 12 }}>
        {d.label}
      </Text>
      {isInput && <Handle type="source" position={Position.Right} />}
    </div>
  );
};

type OperatorData = {
  label: string;
  color: string;
  active: boolean;
  linked: boolean;
  onSelect: () => void;
  onRemove: () => void;
};
const OperatorNode = ({ data }: NodeProps) => {
  const d = data as unknown as OperatorData;
  return (
    <div
      onClick={d.onSelect}
      style={{
        position: 'relative',
        width: 168,
        padding: '8px 10px',
        borderRadius: 8,
        cursor: 'pointer',
        background: 'var(--ant-color-bg-container)',
        border: d.linked
          ? `1.5px solid ${d.active ? '#1677ff' : 'var(--ant-color-border)'}`
          : '1.5px dashed #ff4d4f',
        boxShadow: d.active ? '0 0 0 3px var(--ant-color-primary-bg)' : 'none',
      }}
    >
      <Handle type="target" position={Position.Left} />
      {!d.linked && (
        <span
          style={{
            position: 'absolute',
            top: -9,
            right: 6,
            fontSize: 10,
            lineHeight: '16px',
            color: '#ff4d4f',
            background: 'var(--ant-color-bg-container)',
            padding: '0 4px',
            borderRadius: 4,
          }}
        >
          未接入
        </span>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: d.color,
            flexShrink: 0,
          }}
        />
        <Text ellipsis style={{ fontSize: 12, flex: 1 }}>
          {d.label}
        </Text>
        <Button
          type="text"
          size="small"
          danger
          icon={<DeleteOutlined style={{ fontSize: 11 }} />}
          onClick={(e) => {
            e.stopPropagation();
            d.onRemove();
          }}
        />
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
};

const nodeTypes = {
  // 类型名不能叫 input/output:那是 xyflow 内置类型,style.css 会给包装层加
  // 默认盒样式(150px 宽实线框),套在自定义节点外面造成双框溢出
  endpoint: EndpointNode,
  operator: OperatorNode,
};

const endpointNodes = (outputX: number): Node[] => [
  {
    id: 'input',
    type: 'endpoint',
    data: {},
    position: { x: 0, y: 80 },
    deletable: false,
  },
  {
    id: 'output',
    type: 'endpoint',
    data: {},
    position: { x: outputX, y: 80 },
    deletable: false,
  },
];

const PipelineCanvas: React.FC<{
  steps: DataPlatform.PipelineStep[];
  labelOf: (name: string) => string;
  categoryOf: (name: string) => string;
  activeIdx: number;
  onSelect: (idx: number) => void;
  onRemove: (idx: number) => void;
  /** 主链变化时回传:途经算子在当前 steps 中的原下标排列;比 steps.length 短 = 有游离节点 */
  onOrderChange: (perm: number[]) => void;
  inputLabel: string;
  outputLabel: string;
}> = ({
  steps,
  labelOf,
  categoryOf,
  activeIdx,
  onSelect,
  onRemove,
  onOrderChange,
  inputLabel,
  outputLabel,
}) => {
  const { setNodeRef, isOver } = useDroppable({ id: 'pipeline-dropzone' });

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(
    endpointNodes(STEP_GAP),
  );
  // 边不走 useEdgesState:事件处理器/同步逻辑需要即时读到最新边(edgesRef 镜像)
  const [edges, setEdgesState] = useState<Edge[]>([mkEdge('input', 'output')]);
  const edgesRef = useRef(edges);
  const updateEdges = (next: Edge[]) => {
    edgesRef.current = next;
    setEdgesState(next);
  };

  // stepNodeIdsRef[i] = steps[i] 对应的画布节点 id;prevNamesRef = 上次同步的名序
  const stepNodeIdsRef = useRef<string[]>([]);
  const prevNamesRef = useRef<string[]>([]);
  const nextIdRef = useRef(0);
  // 上次上报是否为「有游离节点」:游离状态只在变化沿上报
  const orphanRef = useRef(false);
  // 自己发出的重排:steps 回流命中该排列时只同步映射,不再上报
  const pendingPermRef = useRef<number[] | null>(null);
  // 始终调用父组件最新一次渲染的回调(节点删除后补报时,旧闭包里的 steps 长度已过期)
  const onOrderChangeRef = useRef(onOrderChange);
  onOrderChangeRef.current = onOrderChange;

  /** 按给定边算主链排列并上报。去重规则:恒等排列不报(唯一例外:上次报过游离,
   *  补报一次解除父组件阻断);游离(排列比 steps 短)只在 无游离→有游离 变化沿报。 */
  const emitOrder = (nextEdges: Edge[]) => {
    const chain = computeChain(nextEdges);
    const idxOf = new Map(
      stepNodeIdsRef.current.map((id, i) => [id, i] as const),
    );
    const perm = chain
      .map((id) => idxOf.get(id))
      .filter((v): v is number => v !== undefined);
    if (perm.length < stepNodeIdsRef.current.length) {
      if (!orphanRef.current) {
        orphanRef.current = true;
        onOrderChangeRef.current(perm);
      }
      return;
    }
    if (perm.every((v, i) => v === i)) {
      if (orphanRef.current) {
        orphanRef.current = false;
        onOrderChangeRef.current(perm);
      }
      return;
    }
    orphanRef.current = false;
    pendingPermRef.current = perm;
    onOrderChangeRef.current(perm);
  };

  // steps → 画布拓扑同步。名序相同(参数编辑/父组件无关重渲)不做任何事;
  // 本 effect 除「删除节点」分支外不调用 emitOrder(防回环第一道闸)。
  useEffect(() => {
    const prevNames = prevNamesRef.current;
    const nextNames = steps.map((s) => s.name);
    if (
      nextNames.length === prevNames.length &&
      nextNames.every((n, i) => n === prevNames[i])
    ) {
      return;
    }

    // 自己发出的重排回流:steps 名序与 pendingPerm 预期一致 → 只同步 id 映射
    const pending = pendingPermRef.current;
    if (pending) {
      pendingPermRef.current = null;
      const expected = pending.map((i) => prevNames[i]);
      if (
        expected.length === nextNames.length &&
        expected.every((n, i) => n === nextNames[i])
      ) {
        stepNodeIdsRef.current = pending.map((i) => stepNodeIdsRef.current[i]);
        prevNamesRef.current = nextNames;
        return;
      }
    }

    // 追加一个(库「+」/拖入):接到主链尾部;链已断则退化为只连出边
    if (
      nextNames.length === prevNames.length + 1 &&
      prevNames.every((n, i) => n === nextNames[i])
    ) {
      const id = `step-${nextIdRef.current++}`;
      stepNodeIdsRef.current = [...stepNodeIdsRef.current, id];
      prevNamesRef.current = nextNames;
      setNodes((nds) => {
        const xs = nds
          .filter((n) => n.id !== 'output')
          .map((n) => n.position.x);
        const x = xs.length ? Math.max(...xs) + STEP_GAP : STEP_GAP;
        return [
          ...nds.map((n) =>
            n.id === 'output'
              ? {
                  ...n,
                  position: {
                    x: Math.max(n.position.x, x + STEP_GAP),
                    y: n.position.y,
                  },
                }
              : n,
          ),
          {
            id,
            type: 'operator',
            data: {},
            position: { x, y: 80 },
            deletable: false,
          },
        ];
      });
      const eds = edgesRef.current;
      const predEdge = eds.find((e) => e.target === 'output');
      if (predEdge) {
        updateEdges([
          ...eds.filter((e) => e !== predEdge),
          mkEdge(predEdge.source, id),
          mkEdge(id, 'output'),
        ]);
      } else {
        const inputHasOut = eds.some((e) => e.source === 'input');
        updateEdges(
          inputHasOut
            ? [...eds, mkEdge(id, 'output')]
            : [...eds, mkEdge('input', id), mkEdge(id, 'output')],
        );
      }
      return;
    }

    // 删掉一个(节点删除按钮):摘节点、桥接前后边。游离节点被删可能让剩余节点
    // 全部归链,需补报一次让父组件解除阻断——emitOrder 自带变化沿去重,且此处
    // 名序已先行同步,上报引发的 steps 回流命中「名序相同」分支即返回,不构成回环。
    if (nextNames.length === prevNames.length - 1) {
      let k = prevNames.findIndex((n, i) => n !== nextNames[i]);
      if (k === -1) k = prevNames.length - 1;
      const restMatch = prevNames
        .filter((_, i) => i !== k)
        .every((n, i) => n === nextNames[i]);
      if (restMatch) {
        const id = stepNodeIdsRef.current[k];
        stepNodeIdsRef.current = stepNodeIdsRef.current.filter(
          (_, i) => i !== k,
        );
        prevNamesRef.current = nextNames;
        setNodes((nds) => nds.filter((n) => n.id !== id));
        const eds = edgesRef.current;
        const inEdge = eds.find((e) => e.target === id);
        const outEdge = eds.find((e) => e.source === id);
        let next = eds.filter((e) => e.source !== id && e.target !== id);
        if (inEdge && outEdge) {
          next = [...next, mkEdge(inEdge.source, outEdge.target)];
        }
        updateEdges(next);
        emitOrder(next);
        return;
      }
    }

    // 整体替换(切成员 Tab、pipelineId 预载等):重建为默认横向链,不上报
    const ids = steps.map(() => `step-${nextIdRef.current++}`);
    stepNodeIdsRef.current = ids;
    prevNamesRef.current = nextNames;
    orphanRef.current = false;
    pendingPermRef.current = null;
    const [inputNode, outputNode] = endpointNodes(
      (steps.length + 1) * STEP_GAP,
    );
    setNodes([
      inputNode,
      ...ids.map((id, i) => ({
        id,
        type: 'operator',
        data: {},
        position: { x: (i + 1) * STEP_GAP, y: 80 },
        deletable: false,
      })),
      outputNode,
    ]);
    const chainNodes = ['input', ...ids, 'output'];
    updateEdges(
      chainNodes.slice(0, -1).map((s, i) => mkEdge(s, chainNodes[i + 1])),
    );
    // updateEdges/emitOrder 为渲染期稳定的本地函数,不列依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps, setNodes]);

  const onConnect = (connection: Connection) => {
    const { source, target } = connection;
    const eds = edgesRef.current;
    if (
      source === target ||
      target === 'input' ||
      source === 'output' ||
      eds.some((e) => e.source === source) ||
      eds.some((e) => e.target === target) ||
      wouldCycle(source, target, eds)
    ) {
      message.warning(LINEAR_WARN);
      return;
    }
    const next = addEdge(
      {
        ...connection,
        type: 'smoothstep',
        markerEnd: { type: MarkerType.ArrowClosed },
      },
      eds,
    );
    updateEdges(next);
    emitOrder(next);
  };

  // 整理画布:主链按当前顺序排回横向链,游离节点排到下方一行;纯布局,不动顺序/连线
  const rfRef = useRef<ReactFlowInstance | null>(null);
  const tidyLayout = () => {
    const chain = computeChain(edgesRef.current);
    const orphans = stepNodeIdsRef.current.filter((id) => !chain.includes(id));
    const pos = new Map<string, { x: number; y: number }>();
    pos.set('input', { x: 0, y: 80 });
    chain.forEach((id, i) => {
      pos.set(id, { x: (i + 1) * STEP_GAP, y: 80 });
    });
    pos.set('output', { x: (chain.length + 1) * STEP_GAP, y: 80 });
    orphans.forEach((id, i) => {
      pos.set(id, { x: (i + 1) * STEP_GAP, y: 220 });
    });
    setNodes((nds) =>
      nds.map((n) => {
        const p = pos.get(n.id);
        return p ? { ...n, position: p } : n;
      }),
    );
    // setNodes 异步落地后再 fitView,否则按旧坐标取景
    window.setTimeout(
      () => rfRef.current?.fitView({ padding: 0.2, duration: 300 }),
      50,
    );
  };

  // 拖动主链节点松手:按 x 坐标重排主链、重建线性连线并上报。仅动主链边,
  // 游离节点间的边原样保留(线性约束下凡触及主链节点的边必属主链,过滤安全)。
  const onNodeDragStop = (_: MouseEvent | TouchEvent, node: Node) => {
    if (node.type !== 'operator') return;
    const eds = edgesRef.current;
    const chain = computeChain(eds);
    if (chain.length < 2 || !chain.includes(node.id)) return;
    const xOf = new Map(nodes.map((n) => [n.id, n.position.x]));
    xOf.set(node.id, node.position.x);
    const sorted = [...chain].sort(
      (a, b) => (xOf.get(a) ?? 0) - (xOf.get(b) ?? 0),
    );
    if (sorted.every((id, i) => id === chain[i])) return;
    const chainNodes = new Set(['input', 'output', ...chain]);
    const kept = eds.filter(
      (e) => !chainNodes.has(e.source) && !chainNodes.has(e.target),
    );
    const path = ['input', ...sorted, 'output'];
    const next = [
      ...kept,
      ...path.slice(0, -1).map((s, i) => mkEdge(s, path[i + 1])),
    ];
    updateEdges(next);
    emitOrder(next);
  };

  // 删边(选中 + Backspace)是调整顺序的入口,删完上报主链;其余变更(选中态)只落库
  const handleEdgesChange = (changes: EdgeChange[]) => {
    const next = applyEdgeChanges(changes, edgesRef.current);
    updateEdges(next);
    if (changes.some((c) => c.type === 'remove')) emitOrder(next);
  };

  // 主链集合(游离节点判定,仅渲染样式用)
  const chainSet = useMemo(() => new Set(computeChain(edges)), [edges]);

  const renderNodes: Node[] = nodes.map((n) => {
    if (n.id === 'input') {
      return {
        ...n,
        data: { label: inputLabel } as unknown as Record<string, unknown>,
      };
    }
    if (n.id === 'output') {
      return {
        ...n,
        data: { label: outputLabel } as unknown as Record<string, unknown>,
      };
    }
    const idx = stepNodeIdsRef.current.indexOf(n.id);
    const step = steps[idx] as DataPlatform.PipelineStep | undefined;
    const data: OperatorData = {
      label: step ? labelOf(step.name) : '',
      color: step
        ? (CATEGORY_COLOR[categoryOf(step.name)] ?? DEFAULT_COLOR)
        : DEFAULT_COLOR,
      active: !!step && idx === activeIdx,
      linked: chainSet.has(n.id),
      onSelect: () => idx >= 0 && onSelect(idx),
      onRemove: () => idx >= 0 && onRemove(idx),
    };
    return { ...n, data: data as unknown as Record<string, unknown> };
  });

  return (
    <div
      ref={setNodeRef}
      style={{
        width: '100%',
        height: '100%',
        borderRadius: 6,
        outline: isOver ? '2px dashed #1677ff' : 'none',
      }}
    >
      <ReactFlow
        nodes={renderNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStop={onNodeDragStop}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        onInit={(inst) => {
          rfRef.current = inst;
        }}
        fitView
        minZoom={0.4}
        fitViewOptions={{ padding: 0.2 }}
      >
        <Background gap={16} />
        <Controls showInteractive={false} />
        <Panel position="top-right">
          <Button
            size="small"
            icon={<NodeIndexOutlined />}
            onClick={tidyLayout}
          >
            整理画布
          </Button>
        </Panel>
      </ReactFlow>
    </div>
  );
};

export default PipelineCanvas;
