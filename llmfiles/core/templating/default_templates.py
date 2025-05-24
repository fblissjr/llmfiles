# llmfiles/core/templating/default_templates.py
"""
Contains the string literals for default and preset Handlebars templates used in llmfiles.
"""

# Note: Using raw strings (r"""...""") for all templates.
# Handlebars {{...}} and {{{...}}} should be used directly.

DEFAULT_MARKDOWN_TEMPLATE = r"""
project root: {{project_path_header_display}}
{{#if show_absolute_project_path}}
(full absolute path: {{project_root_path_absolute}})
{{/if}}

{{#if source_tree}}
project structure (based on included content):
```text
{{source_tree}}
```
{{/if}}

{{#if content_elements}}
content elements:
{{#each content_elements}}
---
element type: {{this.element_type}}
{{#if this.name}}name: {{this.name}}{{/if}}
{{#if this.qualified_name}}qualified name: {{this.qualified_name}}{{/if}}
source file: {{this.file_path}} 
lines: {{this.start_line}}-{{this.end_line}}
language hint: {{this.language}}
{{#if this.docstring}}
docstring:
```
{{this.docstring}}
```
{{/if}}
{{#if this.signature_details}}
signature: {{#if this.signature_details.is_async}}async {{/if}}def {{this.name}}( {{#each this.signature_details.params}}{{this.name}}{{#if this.type_hint}}: {{this.type_hint}}{{/if}}{{#if this.default_value}}={{this.default_value}}{{/if}}{{#unless @last}}, {{/unless}}{{/each}} ){{#with this.signature_details}}{{#if return_type}} -> {{{return_type}}}{{/if}}{{/with}}
{{/if}}
content:
{{{this.llm_formatted_content}}} {{!-- llm_formatted_content is pre-formatted --}}
---
{{/each}}
{{else}}
(no content elements included based on current filters or input.)
{{/if}}

{{!-- Git information sections --}}
{{#if git_diff}}
staged git diff:
```diff
{{git_diff}}
```
{{/if}}
{{#if git_diff_branches}}
git diff ({{git_diff_branch_base}}...{{git_diff_branch_compare}}):
```diff
{{git_diff_branches}}
```
{{/if}}
{{#if git_log_branches}}
git log ({{git_log_branch_base}}...{{git_log_branch_compare}}):
```text
{{git_log_branches}}
```
{{/if}}
{{#if user_vars}}
user variables:
```text
{{#each user_vars}}
- {{ @key }}: {{this}}
{{/each}}
```
{{/if}}
"""

DEFAULT_XML_TEMPLATE = r"""
<llmfiles_context generated_at_utc="{{now}}">
    <project name="<![CDATA[{{project_root_display_name}}]]>" path_in_header="<![CDATA[{{project_path_header_display}}]]>" absolute_path="<![CDATA[{{project_root_path_absolute}}]]>" />
    {{#if source_tree}}<source_tree><![CDATA[{{source_tree}}]]></source_tree>{{/if}}
    
    <content_elements_summary count="{{content_elements.length}}" />
    <content_elements{{#unless content_elements}} message="no content elements were included."{{/unless}}>
    {{#each content_elements}}
        <element 
          type="{{this.element_type}}" 
          {{#if this.name}}name="<![CDATA[{{this.name}}]]>"{{/if}}
          {{#if this.qualified_name}}qualified_name="<![CDATA[{{this.qualified_name}}]]>"{{/if}}
          file_path="<![CDATA[{{this.file_path}}]]>" 
          language="{{this.language}}"
          start_line="{{this.start_line}}" end_line="{{this.end_line}}">
            {{#if this.docstring}}<docstring><![CDATA[{{this.docstring}}]]></docstring>{{/if}}
            {{#if this.signature_details}}
            <signature_info is_async="{{this.signature_details.is_async}}" return_type="<![CDATA[{{this.signature_details.return_type}}]]>">
                {{#each this.signature_details.params}}
                <param name="<![CDATA[{{this.name}}]]>" {{#if this.type_hint}}type_hint="<![CDATA[{{this.type_hint}}]]>"{{/if}} {{#if this.default_value}}default_value="<![CDATA[{{this.default_value}}]]>"{{/if}} />
                {{/each}}
            </signature_info>
            {{/if}}
            <raw_content><![CDATA[{{this.raw_content}}]]></raw_content>
            <llm_formatted_content><![CDATA[{{{this.llm_formatted_content}}}]]></llm_formatted_content>
        </element>
    {{/each}}
    </content_elements>

    {{#if git_diff}}<git_info type="staged_diff"><![CDATA[{{git_diff}}]]></git_info>{{/if}}
    {{#if git_diff_branches}}<git_info type="branch_diff" base="{{git_diff_branch_base}}" compare="{{git_diff_branch_compare}}"><![CDATA[{{git_diff_branches}}]]></git_info>{{/if}}
    {{#if git_log_branches}}<git_info type="branch_log" base="{{git_log_branch_base}}" compare="{{git_log_branch_compare}}"><![CDATA[{{git_log_branches}}]]></git_info>{{/if}}
    {{#if user_vars}}<user_variables>
    {{#each user_vars}}<variable key="<![CDATA[{{@key}}]]>"><![CDATA[{{this}}]]></variable>{{/each}}
    </user_variables>{{/if}}
</llmfiles_context>
"""

PRESET_CLAUDE_OPTIMAL_TEMPLATE = r"""
<documents project_context_display_path="<![CDATA[{{project_path_header_display}}]]>" project_name="<![CDATA[{{project_root_display_name}}]]>">
{{#each content_elements}}
<document index="{{add @index 1}}">
  <source_filename><![CDATA[{{this.file_path}}{{#if this.name}} ({{this.element_type}}: {{this.name}}) lines {{this.start_line}}-{{this.end_line}}{{else}} (whole file){{/if}}]]></source_filename>
  <language_hint><![CDATA[{{this.language}}]]></language_hint>
  {{#if this.qualified_name}}<qualified_name><![CDATA[{{this.qualified_name}}]]></qualified_name>{{/if}}
  {{#if this.docstring}}
  <docstring>
<![CDATA[
{{this.docstring}}
]]>
  </docstring>
  {{/if}}
  {{#if this.signature_details}}
  <signature_info is_async="{{this.signature_details.is_async}}" return_type="<![CDATA[{{this.signature_details.return_type}}]]>">
    params:{{#each this.signature_details.params}}
      - name: {{this.name}}
        {{#if this.type_hint}}type_hint: {{this.type_hint}}{{/if}}
        {{#if this.default_value}}default_value: {{this.default_value}}{{/if}}
    {{/each}}
  </signature_info>
  {{/if}}
  <document_content><![CDATA[{{this.raw_content}}]]></document_content>
</document>
{{/each}}

{{#if source_tree}}
<document index="{{claude_indices.source_tree_idx}}">
  <source_filename>project_structure ({{project_root_display_name}})</source_filename>
  <document_content><![CDATA[{{source_tree}}]]></document_content>
</document>
{{/if}}
{{#if git_diff}}
<document index="{{claude_indices.git_diff_idx}}">
  <source_filename>staged_git_diff ({{project_root_display_name}})</source_filename>
  <document_content><![CDATA[{{git_diff}}]]></document_content>
</document>
{{/if}}
{{#if git_diff_branches}}
<document index="{{claude_indices.git_diff_branches_idx}}">
  <source_filename>git_diff_branches ({{git_diff_branch_base}}...{{git_diff_branch_compare}}) ({{project_root_display_name}})</source_filename>
  <document_content><![CDATA[{{git_diff_branches}}]]></document_content>
</document>
{{/if}}
{{#if git_log_branches}}
<document index="{{claude_indices.git_log_branches_idx}}">
  <source_filename>git_log_branches ({{git_log_branch_base}}...{{git_log_branch_compare}}) ({{project_root_display_name}})</source_filename>
  <document_content><![CDATA[{{git_log_branches}}]]></document_content>
</document>
{{/if}}
{{#if user_vars}}
<document index="{{claude_indices.user_vars_idx}}">
  <source_filename>user_defined_variables ({{project_root_display_name}})</source_filename>
  <document_content><![CDATA[{{#each user_vars}}{{@key}}: {{this}}\n{{/each}}]]></document_content>
</document>
{{/if}}
</documents>
"""