import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '@/services/data-platform';

// Mock ProComponents：把 ProTable 的 request 触发一次以模拟数据加载
vi.mock('@ant-design/pro-components', () => ({
  PageContainer: ({ children }: any) => (
    <div data-testid="page-container">{children}</div>
  ),
  ProTable: ({ columns, headerTitle, request }: any) => {
    request?.({ current: 1, pageSize: 20 }, {}, {});
    return (
      <div data-testid="pro-table">
        <div data-testid="table-title">{headerTitle}</div>
        {columns?.map((col: any) => (
          <div key={col.dataIndex} data-testid={`column-${col.dataIndex}`}>
            {col.title}
          </div>
        ))}
      </div>
    );
  },
}));

vi.mock('@ant-design/icons', () => ({
  InboxOutlined: () => <span data-testid="inbox-icon" />,
}));

vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd');
  const Upload: any = ({ children }: any) => (
    <div data-testid="upload">{children}</div>
  );
  Upload.Dragger = ({ children, accept }: any) => (
    <div data-testid="dragger" data-accept={accept}>
      {children}
    </div>
  );
  Upload.LIST_IGNORE = 'LIST_IGNORE';
  return {
    ...actual,
    Upload,
    message: {
      useMessage: () => [{ success: vi.fn(), error: vi.fn() }, null],
    },
  };
});

import UploadPage from './index';

describe('UploadPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(api, 'listUploads').mockResolvedValue({
      data: [],
      total: 0,
      success: true,
    });
    // 上传页挂载即拉分类下拉选项（#15）——避免真实 request 未注入导致的 rejection
    vi.spyOn(api, 'listCategories').mockResolvedValue({
      data: [],
      success: true,
    });
  });

  it('should render without crashing', () => {
    const { container } = render(<UploadPage />);
    expect(container).toBeTruthy();
  });

  it('should render the dragger with the format whitelist in accept', () => {
    render(<UploadPage />);
    const dragger = screen.getByTestId('dragger');
    expect(dragger).toBeInTheDocument();
    // accept 必须包含 11 种白名单格式
    const accept = dragger.getAttribute('data-accept') ?? '';
    for (const ext of [
      'txt',
      'pdf',
      'ppt',
      'pptx',
      'doc',
      'docx',
      'xlsx',
      'xls',
      'csv',
      'tsv',
      'html',
      'jsonl',
    ]) {
      expect(accept).toContain(`.${ext}`);
    }
  });

  it('should render upload-records table columns', () => {
    render(<UploadPage />);
    expect(screen.getByText('上传记录')).toBeInTheDocument();
    expect(screen.getByText('文件名')).toBeInTheDocument();
    expect(screen.getByText('大小')).toBeInTheDocument();
    expect(screen.getByText('状态')).toBeInTheDocument();
  });

  it('should call listUploads on mount', async () => {
    render(<UploadPage />);
    await waitFor(() => {
      expect(api.listUploads).toHaveBeenCalled();
    });
  });
});
