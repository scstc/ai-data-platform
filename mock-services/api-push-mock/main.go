// api-push-mock 模拟"外部系统"向 AI 数据平台的 API 推送端点投递数据,
// 用于端到端验证「API 推送」类数据源的接入链路。
//
// 平台契约(backend/app/api/v1/ingest_push.py):
//   POST /api/v1/ingest/push/{token}
//   body 支持 {records:[...]} / 裸数组 / jsonl;token 即凭证(无登录态)。
//
// 用法:
//   go run . --push-url "http://127.0.0.1:18003/api/v1/ingest/push/<token>"
//   然后:curl -X POST "http://127.0.0.1:18080/push?n=20"
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

// Record 模拟外部系统产出的一条 LLM 语料记录。
type Record struct {
	ID       string  `json:"id"`
	Text     string  `json:"text"`
	Source   string  `json:"source"`
	Category string  `json:"category"`
	Score    float64 `json:"score"`
	Ts       string  `json:"ts"`
}

var platformURL string // 平台推送 URL(含 token),可被 /push?url= 覆盖

var (
	texts = []string{
		"大语言模型的训练数据质量直接决定下游表现。",
		"The quick brown fox jumps over the lazy dog.",
		"数据接入是构建高质量语料库的第一步。",
		"Retrieval-augmented generation improves factual accuracy.",
		"去重与隐私脱敏是预训练语料治理的关键环节。",
		"Fine-tuning on domain data boosts task performance.",
		"多模态数据需要专门的对齐与清洗流程。",
		"Tokenization choices affect model efficiency and cost.",
	}
	sources    = []string{"crawler", "internal_db", "partner_api", "manual_upload"}
	categories = []string{"news", "qa", "dialogue", "code", "academic"}
)

// genRecords 生成 n 条模拟记录。
func genRecords(n int) []Record {
	out := make([]Record, 0, n)
	now := time.Now().UTC()
	for i := 0; i < n; i++ {
		out = append(out, Record{
			ID:       fmt.Sprintf("rec_%d_%04d", now.Unix(), i),
			Text:     texts[rand.Intn(len(texts))],
			Source:   sources[rand.Intn(len(sources))],
			Category: categories[rand.Intn(len(categories))],
			Score:    float64(rand.Intn(1000)) / 1000.0,
			Ts:       now.Format(time.RFC3339),
		})
	}
	return out
}

// pushToPlatform 把记录投递到平台推送端点,返回 (状态码, 响应体, error)。
// asJSONL=true 时以 jsonl 投递,否则以 {records:[...]} JSON 投递。
func pushToPlatform(target string, recs []Record, asJSONL bool) (int, []byte, error) {
	var body []byte
	var contentType string
	if asJSONL {
		var sb strings.Builder
		for _, r := range recs {
			line, _ := json.Marshal(r)
			sb.Write(line)
			sb.WriteByte('\n')
		}
		body = []byte(sb.String())
		contentType = "application/x-ndjson"
	} else {
		body, _ = json.Marshal(map[string]any{"records": recs})
		contentType = "application/json"
	}
	req, err := http.NewRequest(http.MethodPost, target, bytes.NewReader(body))
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Content-Type", contentType)
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, respBody, nil
}

func qInt(c *gin.Context, key string, def int) int {
	if v := c.Query(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return def
}

func main() {
	port := flag.String("port", "18080", "mock 服务监听端口")
	flag.StringVar(&platformURL, "push-url", os.Getenv("PLATFORM_PUSH_URL"),
		"平台推送 URL(含 token),如 http://127.0.0.1:18003/api/v1/ingest/push/<token>")
	flag.Parse()

	r := gin.Default()

	// 状态/说明
	r.GET("/", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{
			"service":     "api-push-mock",
			"platformURL": platformURL,
			"endpoints": gin.H{
				"GET /sample?n=5":                 "预览生成的模拟记录(不推送)",
				"POST /push?n=10&format=json|jsonl&url=": "生成 n 条并推送到平台,返回平台响应",
			},
		})
	})

	// 预览模拟记录(不推送)
	r.GET("/sample", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"records": genRecords(qInt(c, "n", 5))})
	})

	// 生成并推送到平台
	r.POST("/push", func(c *gin.Context) {
		target := c.Query("url")
		if target == "" {
			target = platformURL
		}
		if target == "" {
			c.JSON(http.StatusBadRequest, gin.H{
				"success": false,
				"message": "未配置平台推送 URL:启动时传 --push-url 或请求带 ?url=",
			})
			return
		}
		n := qInt(c, "n", 10)
		asJSONL := c.Query("format") == "jsonl"
		recs := genRecords(n)
		code, respBody, err := pushToPlatform(target, recs, asJSONL)
		if err != nil {
			c.JSON(http.StatusBadGateway, gin.H{
				"success": false,
				"message": fmt.Sprintf("推送平台失败:%v", err),
			})
			return
		}
		var platformResp any
		_ = json.Unmarshal(respBody, &platformResp)
		c.JSON(http.StatusOK, gin.H{
			"success":        code >= 200 && code < 300,
			"pushedRecords":  n,
			"format":         map[bool]string{true: "jsonl", false: "json"}[asJSONL],
			"platformStatus": code,
			"platformResp":   platformResp,
		})
	})

	addr := ":" + *port
	fmt.Printf("api-push-mock listening on %s, platformURL=%q\n", addr, platformURL)
	_ = r.Run(addr)
}
