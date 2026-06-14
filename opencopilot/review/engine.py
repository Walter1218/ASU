"""ReviewEngine 主入口 — 统一调度三类审查能力"""
import time
import logging
from typing import Optional

from .models import ReviewResult, ReviewType, ReferenceData
from .hallucination import build_hallucination_prompt, parse_hallucination_response
from .data_validator import build_data_check_prompt, parse_data_check_response
from .reference_loader import load_from_file, search_local_files, ReferenceData
from .style_checker import build_style_prompt, parse_style_response

logger = logging.getLogger(__name__)


class ReviewEngine:
    """
    审查引擎主入口

    使用方式:
        engine = ReviewEngine()
        result = engine.review(text, review_type="hallucination")
        result = engine.review(text, review_type="data_check", reference_path="data.xlsx")
        result = engine.review(text, review_type="style", style_target="b2b_formal")
    """

    def review(
        self,
        text: str,
        review_type: str = "hallucination",
        context: str = "",
        reference_path: str = "",
        reference_data: Optional[ReferenceData] = None,
        style_target: str = "",
    ) -> ReviewResult:
        """
        执行审查

        Args:
            text: 待审查文本
            review_type: 审查类型 (hallucination / data_check / style)
            context: 上下文文本（同文档相邻内容，仅 hallucination 用）
            reference_path: 参考文件路径（仅 data_check 用）
            reference_data: 已加载的参考数据（优先于 reference_path）
            style_target: 风格目标（仅 style 用，如 "b2b_formal"）

        Returns:
            ReviewResult
        """
        start = time.time()

        if review_type == ReviewType.HALLUCINATION:
            result = self._review_hallucination(text, context)
        elif review_type == ReviewType.DATA_CHECK:
            result = self._review_data_check(text, reference_path, reference_data)
        elif review_type == ReviewType.STYLE:
            result = self._review_style(text, style_target)
        else:
            result = ReviewResult(
                review_type=review_type,
                summary=f"未知的审查类型: {review_type}"
            )

        result.elapsed_ms = (time.time() - start) * 1000
        return result

    def build_llm_prompt(
        self,
        text: str,
        review_type: str = "hallucination",
        context: str = "",
        reference_path: str = "",
        reference_data: Optional[ReferenceData] = None,
        style_target: str = "",
    ) -> tuple:
        """
        仅构建 LLM prompt（供 Pipeline 中间件使用），不直接调用 LLM。

        Returns:
            (system_prompt, user_prompt)
        """
        if review_type == ReviewType.HALLUCINATION:
            return build_hallucination_prompt(text, context)
        elif review_type == ReviewType.DATA_CHECK:
            ref = reference_data or self._load_reference(reference_path)
            return build_data_check_prompt(text, ref)
        elif review_type == ReviewType.STYLE:
            return build_style_prompt(text, style_target)
        else:
            return ("", f"未知审查类型: {review_type}")

    def parse_llm_response(self, llm_output: str, review_type: str, source_text: str) -> ReviewResult:
        """
        仅解析 LLM 响应（供 Pipeline 中间件使用）。

        Args:
            llm_output: LLM 原始输出
            review_type: 审查类型
            source_text: 原始待审查文本

        Returns:
            ReviewResult
        """
        if review_type == ReviewType.HALLUCINATION:
            return parse_hallucination_response(llm_output, source_text)
        elif review_type == ReviewType.DATA_CHECK:
            return parse_data_check_response(llm_output, source_text)
        elif review_type == ReviewType.STYLE:
            return parse_style_response(llm_output, source_text)
        else:
            return ReviewResult(summary=f"未知审查类型: {review_type}")

    # ---- 内部方法 ----

    def _review_hallucination(self, text: str, context: str = "") -> ReviewResult:
        """幻觉检测：构建 prompt → 调用 LLM → 解析结果"""
        system_prompt, user_prompt = build_hallucination_prompt(text, context)
        llm_output = self._call_llm(system_prompt, user_prompt)
        return parse_hallucination_response(llm_output, text)

    def _review_data_check(
        self, text: str, reference_path: str = "",
        reference_data: Optional[ReferenceData] = None
    ) -> ReviewResult:
        """数据交叉验证：加载参考数据 → 构建 prompt → 调用 LLM → 解析"""
        ref = reference_data or self._load_reference(reference_path)
        if not ref.loaded:
            return ReviewResult(
                review_type="data_check",
                source_text=text,
                summary=f"参考数据加载失败: {ref.error}",
                reference_source=reference_path,
            )

        system_prompt, user_prompt = build_data_check_prompt(text, ref)
        llm_output = self._call_llm(system_prompt, user_prompt)
        result = parse_data_check_response(llm_output, text)
        result.reference_source = ref.source_path
        return result

    def _review_style(self, text: str, style_target: str = "") -> ReviewResult:
        """风格检查：构建 prompt → 调用 LLM → 解析结果"""
        system_prompt, user_prompt = build_style_prompt(text, style_target)
        llm_output = self._call_llm(system_prompt, user_prompt)
        return parse_style_response(llm_output, text)

    def _load_reference(self, path: str) -> ReferenceData:
        """加载参考文档"""
        if not path:
            return ReferenceData(error="未指定参考文件路径")
        return load_from_file(path)

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """调用 LLM（同步，供独立使用时调用；Pipeline 中间件不走此路径）"""
        try:
            from llm_provider import ProviderFactory
            provider = ProviderFactory.create_provider()
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            # 使用同步接口
            full_reply = ""
            for chunk in provider.stream_chat(messages):
                full_reply += chunk
            return full_reply
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return ""
