use crate::tools::ToolKind;

pub(super) fn enabled_tool_names(tools: &[ToolKind]) -> String {
    tools
        .iter()
        .map(|kind| kind.name())
        .collect::<Vec<_>>()
        .join(",")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn enabled_tool_names_joins_in_order() {
        assert_eq!(enabled_tool_names(&[ToolKind::Read]), "read");
        assert_eq!(
            enabled_tool_names(&[ToolKind::Read, ToolKind::Write, ToolKind::Bash]),
            "read,write,bash"
        );
    }
}
