package profile

import (
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
	"unicode"
)

// Expressions such as "total - mono" or "copy_mono + print_mono" (PROMPT 6.4): integers,
// identifiers of other counters, + - * and parentheses.

type exprNode interface {
	eval(vars map[string]int64) (int64, error)
	refs(out map[string]bool)
}

type numNode int64

func (n numNode) eval(map[string]int64) (int64, error) { return int64(n), nil }
func (numNode) refs(map[string]bool)                   {}

type refNode string

func (r refNode) eval(vars map[string]int64) (int64, error) {
	v, ok := vars[string(r)]
	if !ok {
		return 0, errMissingRef{string(r)}
	}
	return v, nil
}
func (r refNode) refs(out map[string]bool) { out[string(r)] = true }

type binNode struct {
	op          byte
	left, right exprNode
}

var errOverflow = errors.New("estouro numérico na expressão")

func (b binNode) eval(vars map[string]int64) (int64, error) {
	l, err := b.left.eval(vars)
	if err != nil {
		return 0, err
	}
	r, err := b.right.eval(vars)
	if err != nil {
		return 0, err
	}
	switch b.op {
	case '+':
		if (r > 0 && l > math.MaxInt64-r) || (r < 0 && l < math.MinInt64-r) {
			return 0, errOverflow
		}
		return l + r, nil
	case '-':
		if (r < 0 && l > math.MaxInt64+r) || (r > 0 && l < math.MinInt64+r) {
			return 0, errOverflow
		}
		return l - r, nil
	default:
		if l != 0 && r != 0 && (l*r)/r != l {
			return 0, errOverflow
		}
		return l * r, nil
	}
}

func (b binNode) refs(out map[string]bool) {
	b.left.refs(out)
	b.right.refs(out)
}

type errMissingRef struct{ name string }

func (e errMissingRef) Error() string { return fmt.Sprintf("contador %q não disponível", e.name) }

type exprParser struct {
	toks []string
	pos  int
}

func tokenize(s string) ([]string, error) {
	var toks []string
	for i := 0; i < len(s); {
		c := rune(s[i])
		switch {
		case unicode.IsSpace(c):
			i++
		case strings.ContainsRune("+-*()", c):
			toks = append(toks, string(c))
			i++
		case unicode.IsDigit(c) || unicode.IsLetter(c) || c == '_':
			j := i
			for j < len(s) && (unicode.IsDigit(rune(s[j])) || unicode.IsLetter(rune(s[j])) || s[j] == '_') {
				j++
			}
			toks = append(toks, s[i:j])
			i = j
		default:
			return nil, fmt.Errorf("caractere inválido %q na expressão", c)
		}
	}
	return toks, nil
}

func parseExpr(s string) (exprNode, error) {
	toks, err := tokenize(s)
	if err != nil {
		return nil, err
	}
	if len(toks) == 0 {
		return nil, errors.New("expressão vazia")
	}
	p := &exprParser{toks: toks}
	n, err := p.sum()
	if err != nil {
		return nil, fmt.Errorf("expressão %q: %w", s, err)
	}
	if p.pos != len(p.toks) {
		return nil, fmt.Errorf("expressão %q: sobra %q", s, p.toks[p.pos])
	}
	return n, nil
}

func (p *exprParser) peek() string {
	if p.pos < len(p.toks) {
		return p.toks[p.pos]
	}
	return ""
}

func (p *exprParser) sum() (exprNode, error) {
	left, err := p.product()
	if err != nil {
		return nil, err
	}
	for p.peek() == "+" || p.peek() == "-" {
		op := p.toks[p.pos][0]
		p.pos++
		right, err := p.product()
		if err != nil {
			return nil, err
		}
		left = binNode{op: op, left: left, right: right}
	}
	return left, nil
}

func (p *exprParser) product() (exprNode, error) {
	left, err := p.atom()
	if err != nil {
		return nil, err
	}
	for p.peek() == "*" {
		p.pos++
		right, err := p.atom()
		if err != nil {
			return nil, err
		}
		left = binNode{op: '*', left: left, right: right}
	}
	return left, nil
}

func (p *exprParser) atom() (exprNode, error) {
	t := p.peek()
	switch {
	case t == "":
		return nil, errors.New("fim inesperado")
	case t == "(":
		p.pos++
		n, err := p.sum()
		if err != nil {
			return nil, err
		}
		if p.peek() != ")" {
			return nil, errors.New("falta ')'")
		}
		p.pos++
		return n, nil
	case t == "-":
		p.pos++
		n, err := p.atom()
		if err != nil {
			return nil, err
		}
		return binNode{op: '-', left: numNode(0), right: n}, nil
	case unicode.IsDigit(rune(t[0])):
		v, err := strconv.ParseInt(t, 10, 64)
		if err != nil {
			return nil, fmt.Errorf("número inválido %q", t)
		}
		p.pos++
		return numNode(v), nil
	case unicode.IsLetter(rune(t[0])) || t[0] == '_':
		p.pos++
		return refNode(t), nil
	default:
		return nil, fmt.Errorf("símbolo inesperado %q", t)
	}
}
