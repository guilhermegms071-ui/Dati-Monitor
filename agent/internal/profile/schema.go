package profile

import (
	"bytes"
	_ "embed"
	"errors"
	"fmt"
	"strings"
	"sync"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

// schemaJSON is a copy of profiles/profile.schema.json (kept in sync by go generate + a test).
//
//go:embed profile.schema.json
var schemaJSON []byte

var (
	schemaOnce sync.Once
	schema     *jsonschema.Schema
	schemaErr  error
)

func compiledSchema() (*jsonschema.Schema, error) {
	schemaOnce.Do(func() {
		doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(schemaJSON))
		if err != nil {
			schemaErr = fmt.Errorf("schema de perfil inválido: %w", err)
			return
		}
		c := jsonschema.NewCompiler()
		const url = "profile.schema.json"
		if err := c.AddResource(url, doc); err != nil {
			schemaErr = err
			return
		}
		schema, schemaErr = c.Compile(url)
	})
	return schema, schemaErr
}

// ValidateJSON validates a profile document against the shared JSON Schema.
func ValidateJSON(raw []byte) error {
	s, err := compiledSchema()
	if err != nil {
		return err
	}
	inst, err := jsonschema.UnmarshalJSON(bytes.NewReader(raw))
	if err != nil {
		return fmt.Errorf("perfil não é JSON válido: %w", err)
	}
	if err := s.Validate(inst); err != nil {
		var verr *jsonschema.ValidationError
		if errors.As(err, &verr) {
			return fmt.Errorf("perfil fora do schema: %s", strings.ReplaceAll(verr.Error(), "\n", "; "))
		}
		return err
	}
	return nil
}
